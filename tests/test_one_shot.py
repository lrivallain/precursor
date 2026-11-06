"""The shared one-shot completion helper (issue #333).

Every "ask the model once, no tools" feature goes through
:func:`~precursor.backend.services.llm.one_shot.complete_once`, so these pin the
behaviour they all inherit: usage attribution, connection release, model
resolution and the single error type.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import Chat, Topic, UsageRecord
from precursor.backend.services.llm import one_shot
from precursor.backend.services.llm.base import (
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    ToolDef,
    UsageEvent,
)
from precursor.backend.services.llm.one_shot import (
    LLMCallFailed,
    complete_once,
    complete_with_tools,
)


@pytest.fixture(autouse=True, scope="module")
def _db() -> None:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass


class _Provider:
    """Scripted provider recording what it was asked and what it saw."""

    name = "fake"

    def __init__(
        self,
        *,
        text: str = "reply",
        usage: UsageEvent | None = None,
        error: Exception | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._text = text
        self._usage = usage
        self._error = error
        self._session = session
        self.calls: list[dict[str, Any]] = []
        self.in_transaction: bool | None = None

    async def stream_chat_with_tools(self, **kwargs: Any) -> AsyncIterator[object]:
        self.calls.append(kwargs)
        if self._session is not None:
            self.in_transaction = self._session.in_transaction()
        if self._error is not None:
            raise self._error
        yield TextDeltaEvent(content=f"  {self._text}  ")
        if self._usage is not None:
            yield self._usage


def _use(monkeypatch: pytest.MonkeyPatch, provider: _Provider) -> None:
    async def _get(_session: AsyncSession, **_kwargs: Any) -> _Provider:
        return provider

    monkeypatch.setattr(one_shot, "get_llm_provider", _get)


async def _containers() -> tuple[int, int]:
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as session:
        topic = Topic(title="One-shot", slug=f"one-shot-{suffix}")
        chat = Chat(title="One-shot", slug=f"one-shot-chat-{suffix}")
        session.add_all([topic, chat])
        await session.commit()
        return topic.id, chat.id


async def _ledger_count() -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(UsageRecord)) or 0)


async def _latest_usage() -> UsageRecord:
    async with SessionLocal() as session:
        return (
            await session.execute(select(UsageRecord).order_by(UsageRecord.id.desc()).limit(1))
        ).scalar_one()


@pytest.mark.parametrize("container", ["topic", "chat"])
async def test_usage_lands_in_the_ledger_attributed_to_the_container(
    monkeypatch: pytest.MonkeyPatch, container: str
) -> None:
    topic_id, chat_id = await _containers()
    provider = _Provider(usage=UsageEvent(prompt_tokens=7, completion_tokens=3, total_tokens=10))
    _use(monkeypatch, provider)
    ids = {"topic_id": topic_id} if container == "topic" else {"chat_id": chat_id}

    async with SessionLocal() as session:
        result = await complete_once(
            session, system="sys", user="usr", usage_source="/probe", model="m-1", **ids
        )

    assert (result.text, result.model) == ("reply", "m-1")
    assert result.usage is not None and result.usage.total_tokens == 10
    system, user = provider.calls[0]["messages"]
    assert (system.role, system.content, user.role, user.content) == (
        "system",
        "sys",
        "user",
        "usr",
    )
    row = await _latest_usage()
    assert (row.source, row.model, row.total_tokens) == ("/probe", "m-1", 10)
    assert (row.topic_id, row.chat_id) == (
        (topic_id, None) if container == "topic" else (None, chat_id)
    )


async def test_usage_survives_the_caller_rolling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ledger is written in its own session, not the caller's."""
    _use(
        monkeypatch,
        _Provider(usage=UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
    )
    before = await _ledger_count()
    async with SessionLocal() as session:
        await complete_once(session, system="s", user="u", usage_source="/probe", model="m")
        await session.rollback()
    assert await _ledger_count() == before + 1


async def test_no_usage_event_writes_no_ledger_row(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, _Provider(usage=None))
    before = await _ledger_count()
    async with SessionLocal() as session:
        result = await complete_once(session, system="s", user="u", usage_source="/probe")
    assert result.usage is None
    assert await _ledger_count() == before


@pytest.mark.parametrize("release", [True, False])
async def test_the_callers_connection_is_released_before_the_provider_runs(
    monkeypatch: pytest.MonkeyPatch, release: bool
) -> None:
    topic_id, _ = await _containers()
    async with SessionLocal() as session:
        # A read opens a transaction that holds a pooled connection.
        assert await session.get(Topic, topic_id) is not None
        assert session.in_transaction()
        provider = _Provider(session=session)
        _use(monkeypatch, provider)
        await complete_once(
            session, system="s", user="u", usage_source="/probe", release_connection=release
        )
    assert provider.in_transaction is (not release)


async def test_the_model_defaults_to_the_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _chat_model(_session: AsyncSession) -> str:
        return "chat-model"

    monkeypatch.setattr(one_shot, "resolve_llm_model", _chat_model)
    provider = _Provider()
    _use(monkeypatch, provider)
    async with SessionLocal() as session:
        default = await complete_once(session, system="s", user="u", usage_source="/probe")
        chosen = await complete_once(
            session,
            system="s",
            user="u",
            usage_source="/probe",
            model="fast-model",
            reasoning_effort="low",
        )
    assert (default.model, chosen.model) == ("chat-model", "fast-model")
    assert [c["model"] for c in provider.calls] == ["chat-model", "fast-model"]
    assert [c["reasoning_effort"] for c in provider.calls] == [None, "low"]
    assert all(c["tools"] == [] for c in provider.calls)


async def test_a_failing_completion_raises_llm_call_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _Provider(error=RuntimeError("upstream is down")))
    async with SessionLocal() as session:
        with pytest.raises(LLMCallFailed) as caught:
            await complete_once(session, system="s", user="u", usage_source="/probe")
    assert str(caught.value) == "upstream is down"
    assert caught.value.source == "/probe"
    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_a_failing_provider_lookup_raises_llm_call_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(_session: AsyncSession, **_kwargs: Any) -> _Provider:
        raise RuntimeError("no provider")

    monkeypatch.setattr(one_shot, "get_llm_provider", _boom)
    async with SessionLocal() as session:
        with pytest.raises(LLMCallFailed, match="no provider"):
            await complete_once(session, system="s", user="u", usage_source="/probe")


async def test_an_unhandled_failure_is_a_502_from_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routers that don't catch it get the app-level mapping."""
    topic_id, _ = await _containers()
    _use(monkeypatch, _Provider(error=RuntimeError("upstream is down")))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/topics/{topic_id}/commands/notes/rephrase",
            json={"text": "rough notes", "instruction": None},
        )
    assert resp.status_code == 502
    assert resp.json() == {"detail": "LLM call failed: upstream is down"}


class _Scripted:
    """A provider that asks for a tool on each round until its tools are withdrawn."""

    name = "scripted"

    def __init__(self, rounds_with_tools: int) -> None:
        self._left = rounds_with_tools
        self.calls: list[dict[str, Any]] = []

    async def stream_chat_with_tools(self, **kwargs: Any) -> AsyncIterator[object]:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if kwargs["tools"] and self._left > 0:
            self._left -= 1
            yield ToolCallsEvent(calls=[ToolCallRequest(id="c1", name="peek", arguments="{}")])
        else:
            yield TextDeltaEvent(content="verdict")
        yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)


_PEEK = ToolDef(name="peek", description="d", parameters={"type": "object", "properties": {}})


async def test_a_tool_loop_feeds_results_back_and_returns_the_final_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Scripted(rounds_with_tools=1)
    _use(monkeypatch, provider)  # type: ignore[arg-type]
    ran: list[str] = []

    async def _run(call: ToolCallRequest) -> str:
        ran.append(call.name)
        return "file text"

    before = await _ledger_count()
    async with SessionLocal() as session:
        result = await complete_with_tools(
            session, system="s", user="u", usage_source="/probe", tools=[_PEEK], run_tool=_run
        )
    assert result.text == "verdict" and ran == ["peek"]
    roles = [m.role for m in provider.calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert provider.calls[1]["messages"][-1].content == "file text"
    assert await _ledger_count() == before + 2


async def test_a_tool_loop_withdraws_the_tools_so_the_model_must_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Scripted(rounds_with_tools=99)
    _use(monkeypatch, provider)  # type: ignore[arg-type]

    async def _run(_call: ToolCallRequest) -> str:
        return "x"

    async with SessionLocal() as session:
        result = await complete_with_tools(
            session,
            system="s",
            user="u",
            usage_source="/probe",
            tools=[_PEEK],
            run_tool=_run,
            max_rounds=2,
        )
    assert result.text == "verdict"
    assert [bool(c["tools"]) for c in provider.calls] == [True, True, False]
    assert "final answer" in provider.calls[-1]["messages"][-1].content


async def test_a_tool_loop_failure_is_a_single_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _Provider(error=RuntimeError("boom")))

    async def _run(_call: ToolCallRequest) -> str:
        return "x"

    async with SessionLocal() as session:
        with pytest.raises(LLMCallFailed, match="boom"):
            await complete_with_tools(
                session, system="s", user="u", usage_source="/probe", tools=[], run_tool=_run
            )
