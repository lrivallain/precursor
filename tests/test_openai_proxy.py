"""The OpenAI-compatible endpoint relays OpenAI clients to the active provider.

A client configured with Precursor's base URL and key must see exactly the wire
format it would get from OpenAI — streamed or not, with tools — while the
endpoint stays shut unless the user turned it on, holds the key, and runs a
provider it can relay to.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting, UsageRecord
from precursor.backend.services.llm.base import (
    LLMError,
    LLMModel,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.openai_proxy import USAGE_SOURCE

_SETTING_KEYS = ("openai_proxy_enabled", "openai_proxy_key", "llm_provider")


class _Provider:
    name = "fake"

    def __init__(self, events: list[Any] | None = None, *, error: Exception | None = None):
        self.events = events if events is not None else _reply("Hello")
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def list_models(self) -> list[LLMModel]:
        return [
            LLMModel(
                id="gpt-test",
                name="GPT Test",
                publisher="OpenAI",
                context_window=128000,
                max_output_tokens=16000,
                supported_reasoning_efforts=["low", "high"],
            ),
            LLMModel(id="org/other", name="Other"),
        ]

    async def stream_chat_with_tools(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event


def _reply(text: str) -> list[Any]:
    return [
        TextDeltaEvent(content=text),
        UsageEvent(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        TurnDoneEvent(finish_reason="stop"),
    ]


class _Upstream(Exception):
    """Stands in for the SDK's status error that providers chain from."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"upstream {status_code}")
        self.status_code = status_code


async def _reset() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key.in_(_SETTING_KEYS)))
        await session.execute(delete(UsageRecord).where(UsageRecord.source == USAGE_SOURCE))
        await session.commit()


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    # Entering the client runs the lifespan, which creates the tables.
    with TestClient(create_app()) as c:
        await _reset()
        yield c
    await _reset()


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Provider]:
    fake = _Provider()

    async def _get(_session: Any, **_kw: Any) -> _Provider:
        return fake

    async def _token(_session: Any) -> str:
        return "gho_test"

    monkeypatch.setattr("precursor.backend.services.openai_proxy.get_llm_provider", _get)
    monkeypatch.setattr("precursor.backend.services.openai_proxy.resolve_github_token", _token)
    yield fake


def _enable(client: TestClient) -> dict[str, str]:
    key = client.put("/api/settings", json={"openai_proxy_enabled": True}).json()[
        "openai_proxy_key"
    ]
    return {"Authorization": f"Bearer {key}"}


def _data_lines(body: str) -> list[str]:
    return [block[len("data: ") :] for block in body.split("\n\n") if block.startswith("data: ")]


def test_endpoint_is_off_by_default(client: TestClient, provider: _Provider) -> None:
    settings = client.get("/api/settings").json()
    assert settings["openai_proxy_enabled"] is False
    assert settings["openai_proxy_key"] == ""
    assert settings["openai_proxy_url"].endswith("/api/openai/v1")

    resp = client.get("/api/openai/v1/models", headers={"Authorization": "Bearer anything"})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "endpoint_disabled"


def test_enabling_mints_a_recoverable_key(client: TestClient, provider: _Provider) -> None:
    headers = _enable(client)
    key = headers["Authorization"].removeprefix("Bearer ")

    assert key.startswith("sk-precursor-")
    # Readable again later: the user pastes it into more than one client.
    assert client.get("/api/settings").json()["openai_proxy_key"] == key
    # Re-saving the switch keeps the key clients already hold.
    client.put("/api/settings", json={"openai_proxy_enabled": True})
    assert client.get("/api/settings").json()["openai_proxy_key"] == key


def test_requests_need_the_key(client: TestClient, provider: _Provider) -> None:
    _enable(client)

    missing = client.get("/api/openai/v1/models")
    wrong = client.get("/api/openai/v1/models", headers={"Authorization": "Bearer sk-nope"})

    for resp in (missing, wrong):
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_api_key"
        assert resp.headers["www-authenticate"] == "Bearer"
    assert provider.calls == []


def test_regenerating_the_key_revokes_the_old_one(client: TestClient, provider: _Provider) -> None:
    old = _enable(client)
    new_key = client.post("/api/settings/openai-proxy/key").json()["key"]

    assert client.get("/api/openai/v1/models", headers=old).status_code == 401
    ok = client.get("/api/openai/v1/models", headers={"Authorization": f"Bearer {new_key}"})
    assert ok.status_code == 200


def test_models_are_listed_in_openai_shape(client: TestClient, provider: _Provider) -> None:
    headers = _enable(client)

    body = client.get("/api/openai/v1/models", headers=headers).json()

    assert body["object"] == "list"
    first = body["data"][0]
    assert first["id"] == "gpt-test"
    assert first["object"] == "model"
    assert first["owned_by"] == "OpenAI"
    assert first["context_window"] == 128000
    assert first["max_output_tokens"] == 16000
    # Ids with a slash (Hugging Face, Ollama namespaces) must still resolve.
    one = client.get("/api/openai/v1/models/org/other", headers=headers)
    assert one.json()["id"] == "org/other"
    missing = client.get("/api/openai/v1/models/nope", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "model_not_found"


@pytest.mark.parametrize(
    ("provider_id", "needle"),
    [("mock", "Mock (offline)"), ("azure_foundry", "Azure AI Foundry")],
)
def test_unsupported_providers_disable_the_endpoint(
    client: TestClient, provider: _Provider, provider_id: str, needle: str
) -> None:
    headers = _enable(client)
    client.put("/api/settings", json={"llm_provider": provider_id})

    settings = client.get("/api/settings").json()
    assert settings["openai_proxy_available"] is False
    assert needle in settings["openai_proxy_unavailable_reason"]
    resp = client.get("/api/openai/v1/models", headers=headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "provider_unavailable"


def test_copilot_without_a_token_is_unavailable(
    client: TestClient, provider: _Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_token(_session: Any) -> str:
        return ""

    monkeypatch.setattr("precursor.backend.services.openai_proxy.resolve_github_token", _no_token)
    headers = _enable(client)

    resp = client.post(
        "/api/openai/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
    )

    # The mock would otherwise answer the client with canned text.
    assert resp.status_code == 503
    assert "GitHub token" in resp.json()["error"]["message"]
    assert provider.calls == []


def test_supported_providers_are_flagged_in_the_catalog(client: TestClient) -> None:
    flags = {p["id"]: p["openai_proxy"] for p in client.get("/api/llm/providers").json()}

    assert flags["github_copilot"] is True
    assert flags["openai"] is True
    assert flags["mock"] is False
    assert flags["azure_foundry"] is False


async def test_non_streamed_completion(client: TestClient, provider: _Provider) -> None:
    headers = _enable(client)

    resp = client.post(
        "/api/openai/v1/chat/completions",
        headers=headers,
        json={
            "model": "gpt-test",
            "temperature": 0.3,
            "tool_choice": "auto",
            "user": "someone",
            "messages": [
                {"role": "developer", "content": "Be brief."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is"},
                        {"type": "text", "text": "this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                    ],
                },
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-test"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hello"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}

    call = provider.calls[0]
    system, user = call["messages"]
    assert (system.role, system.content) == ("system", "Be brief.")
    assert (user.content, user.image_urls) == ("What is\nthis?", ["data:image/png;base64,x"])
    # Sampling controls reach the provider; tool_choice without tools, and
    # account-side fields like ``user``, don't.
    assert call["request_options"] == {"temperature": 0.3}

    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(UsageRecord).where(UsageRecord.source == USAGE_SOURCE)))
            .scalars()
            .all()
        )
    assert [(r.model, r.total_tokens) for r in rows] == [("gpt-test", 10)]


def test_tool_round_trip(client: TestClient, provider: _Provider) -> None:
    headers = _enable(client)
    provider.events = [
        ToolCallsEvent(calls=[ToolCallRequest(id="call_1", name="lookup", arguments='{"q":"x"}')]),
        TurnDoneEvent(finish_reason="tool_calls"),
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look something up",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
    ]

    body = client.post(
        "/api/openai/v1/chat/completions",
        headers=headers,
        json={
            "model": "gpt-test",
            "tools": tools,
            "tool_choice": "auto",
            "messages": [
                {"role": "user", "content": "find x"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"q":"w"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_0", "content": "nothing"},
            ],
        },
    ).json()

    message = body["choices"][0]["message"]
    assert message["content"] is None
    assert message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"q":"x"}'},
        }
    ]
    assert body["choices"][0]["finish_reason"] == "tool_calls"

    call = provider.calls[0]
    assert [t.name for t in call["tools"]] == ["lookup"]
    assert call["request_options"] == {"tool_choice": "auto"}
    assistant, tool = call["messages"][1:]
    assert assistant.tool_calls[0]["function"]["name"] == "lookup"
    assert (tool.role, tool.tool_call_id, tool.content) == ("tool", "call_0", "nothing")


def test_streamed_completion(client: TestClient, provider: _Provider) -> None:
    headers = _enable(client)
    provider.events = [
        ReasoningDeltaEvent(content="thinking"),
        TextDeltaEvent(content="Hel"),
        TextDeltaEvent(content="lo"),
        ToolCallsEvent(calls=[ToolCallRequest(id="call_1", name="lookup", arguments="{}")]),
        UsageEvent(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        TurnDoneEvent(finish_reason="tool_calls"),
    ]

    resp = client.post(
        "/api/openai/v1/chat/completions",
        headers=headers,
        json={
            "model": "gpt-test",
            "stream": True,
            "stream_options": {"include_usage": True},
            "reasoning_effort": "high",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    lines = _data_lines(resp.text)
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(line) for line in lines[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    # One completion: every chunk shares the id.
    assert len({c["id"] for c in chunks}) == 1
    deltas = [c["choices"][0]["delta"] for c in chunks if c["choices"]]
    assert deltas[0] == {"role": "assistant", "content": ""}
    assert {"reasoning_content": "thinking"} in deltas
    assert "".join(d.get("content", "") for d in deltas) == "Hello"
    tool_delta = next(d for d in deltas if "tool_calls" in d)
    assert tool_delta["tool_calls"][0]["index"] == 0
    assert tool_delta["tool_calls"][0]["function"]["name"] == "lookup"
    finish = [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]]
    assert finish[-1] == "tool_calls"
    # include_usage: a trailing choice-less chunk carries the totals.
    assert chunks[-1]["choices"] == []
    assert chunks[-1]["usage"]["total_tokens"] == 6
    assert provider.calls[0]["reasoning_effort"] == "high"


def test_tool_result_images_follow_the_tool_results(
    client: TestClient, provider: _Provider
) -> None:
    headers = _enable(client)
    call = {"type": "function", "function": {"name": "shot", "arguments": "{}"}}
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}

    resp = client.post(
        "/api/openai/v1/chat/completions",
        headers=headers,
        json={
            "model": "gpt-test",
            "tools": [{"type": "function", "function": {"name": "shot"}}],
            "messages": [
                {"role": "user", "content": "screenshot both"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "a", **call}, {"id": "b", **call}],
                },
                # VS Code returns a screenshot as parts of the tool result.
                {
                    "role": "tool",
                    "tool_call_id": "a",
                    "content": [{"type": "text", "text": "A"}, image],
                },
                {"role": "tool", "tool_call_id": "b", "content": [image]},
            ],
        },
    )

    assert resp.status_code == 200
    messages = provider.calls[0]["messages"]
    assert [m.role for m in messages] == ["user", "assistant", "tool", "tool", "user"]
    # Tool results stay text, right after the calls they answer...
    assert (messages[2].content, messages[2].image_urls) == ("A", [])
    assert messages[3].image_urls == []
    assert messages[3].content
    # ...and their images follow as a user turn, which every provider accepts.
    assert messages[4].image_urls == ["data:image/png;base64,x", "data:image/png;base64,x"]


@pytest.mark.parametrize(
    ("upstream_status", "expected"),
    [(400, 400), (429, 429), (401, 502), (500, 502)],
)
def test_provider_refusals_keep_meaningful_statuses(
    client: TestClient, provider: _Provider, upstream_status: int, expected: int
) -> None:
    headers = _enable(client)
    error = LLMError("The model provider rejected the request: nope")
    error.__cause__ = _Upstream(upstream_status)
    provider.error = error

    resp = client.post(
        "/api/openai/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-test", "stream": True, "messages": [{"role": "user", "content": "x"}]},
    )

    # Refused while opening, so even a streamed request gets a real status —
    # and Precursor's own credential failures never read as the client's key.
    assert resp.status_code == expected
    assert "rejected the request" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    ("body", "param"),
    [
        ({"messages": [{"role": "user", "content": "x"}]}, "model"),
        ({"model": "m", "messages": []}, "messages"),
        ({"model": "m", "n": 2, "messages": [{"role": "user", "content": "x"}]}, "n"),
        (
            {
                "model": "m",
                "messages": [
                    {"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}
                ],
            },
            "messages[0].content[0]",
        ),
        ({"model": "m", "messages": [{"role": "function", "content": "x"}]}, "messages[0].role"),
    ],
)
def test_malformed_requests_are_rejected(
    client: TestClient, provider: _Provider, body: dict[str, Any], param: str
) -> None:
    headers = _enable(client)

    resp = client.post("/api/openai/v1/chat/completions", headers=headers, json=body)

    assert resp.status_code == 400
    assert resp.json()["error"]["param"] == param
    assert provider.calls == []
