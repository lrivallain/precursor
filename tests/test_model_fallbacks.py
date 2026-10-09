"""Category fallback policy, provider retries and SDK recovery without real LLM calls."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentRun, AgentSession, AppSetting
from precursor.backend.schemas.model_fallback import ModelCategories, ModelPreset
from precursor.backend.schemas.settings import SettingsPayload
from precursor.backend.services.agents.live_session import _LiveSession
from precursor.backend.services.agents.manager import AgentManager
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    LLMModel,
    TextDeltaEvent,
    UsageEvent,
)
from precursor.backend.services.llm.fallback import CategoryFallbackProvider
from precursor.backend.services.model_fallbacks import (
    ModelSelection,
    category_presets,
    is_model_rejection,
)

REJECTION = (
    'reasoning_effort "high" was provided, but model haiku does not support reasoning effort'
)


def _preset(model: str, effort: str = "", tokens: int = 128_000, tier: str = "default"):
    return ModelPreset(
        model=model, reasoning_effort=effort, context_tokens=tokens, context_tier=tier
    )


def test_category_matching_prefers_exact_configuration_and_never_guesses() -> None:
    fast = _preset("same", "low", 32_000)
    smart = _preset("same", "high", 128_000)
    categories = ModelCategories(efficiency=[fast], intelligence=[smart])
    assert category_presets(categories, smart) == [smart]
    assert category_presets(categories, ModelSelection("same", "medium")) == []
    assert category_presets(categories, ModelSelection("unassigned")) == []
    assert category_presets(ModelCategories(efficiency=[fast]), ModelSelection("same", "high")) == [
        fast
    ]


def test_agent_category_matches_context_tier_not_chat_budget() -> None:
    fast = _preset("same", "low")
    smart = _preset("same", "high", tier="long_context")
    categories = ModelCategories(efficiency=[fast], intelligence=[smart])
    selection = ModelSelection("same", "high", 32_000, "long_context")
    assert category_presets(categories, selection, agents=True) == [smart]


@pytest.mark.parametrize(
    "error",
    [
        REJECTION,
        "invalid_reasoning_effort",
        "model_not_found",
        "model_not_available_for_integrator",
        "Unknown model xyz",
        "Model xyz does not exist",
        "invalid_context_tier",
        "context_length_exceeded",
    ],
)
def test_recoverable_rejections(error: str) -> None:
    assert is_model_rejection(error)


@pytest.mark.parametrize(
    "error",
    [
        "bad credentials",
        "rate_limit_exceeded",
        "quota exceeded",
        "timeout",
        "Too many tools for this model",
        "server error",
        "authentication failed: model_not_found",
    ],
)
def test_unrelated_failures_are_not_retried(error: str) -> None:
    assert not is_model_rejection(error)


@pytest.mark.parametrize(
    "config",
    [
        {"unknown": {"efficiency": [{"model": "a"}]}},
        {"agents": {"efficiency": [{"model": "auto"}]}},
        {"openai": {"efficiency": [{"model": " "}]}},
        {"openai": {"efficiency": [{"model": "a", "reasoning_effort": "invalid"}]}},
        {"openai": {"efficiency": [{"model": "a", "context_tokens": 0}]}},
        {"openai": {"efficiency": [{"model": "a"}], "balanced": [{"model": "a"}]}},
        {
            "agents": {
                "efficiency": [{"model": "a", "context_tokens": 32_000}],
                "balanced": [{"model": "a", "context_tokens": 128_000}],
            }
        },
        {
            "openai": {
                "efficiency": [{"model": "a", "context_tier": "default"}],
                "balanced": [{"model": "a", "context_tier": "long_context"}],
            }
        },
        {"openai": {"efficiency": [{"model": str(i)} for i in range(13)]}},
    ],
)
def test_invalid_category_settings_are_rejected(config: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SettingsPayload(model_fallbacks=config)


class _Provider:
    name = "openai"

    def __init__(self, offered: list[str] | None = None) -> None:
        self.offered = offered
        self.failures: dict[tuple[str, str], str] = {}
        self.calls: list[dict[str, Any]] = []
        self.partial = False

    async def list_models(self) -> list[LLMModel]:
        if self.offered is None:
            raise RuntimeError("catalogue offline")
        return [LLMModel(id=m, name=m, context_window=32_000) for m in self.offered]

    async def stream_chat_with_tools(
        self, **kwargs: Any
    ) -> AsyncIterator[TextDeltaEvent | UsageEvent]:
        self.calls.append(kwargs)
        error = self.failures.get((kwargs["model"], kwargs["reasoning_effort"] or ""))
        if error:
            if self.partial:
                yield TextDeltaEvent(content="partial")
            raise LLMError(error)
        yield TextDeltaEvent(content="done")
        yield UsageEvent(prompt_tokens=7, completion_tokens=3, total_tokens=10)


async def _call(wrapper: CategoryFallbackProvider, model: str = "haiku", effort: str = "high"):
    return [
        e
        async for e in wrapper.stream_chat_with_tools(
            model=model,
            reasoning_effort=effort,
            messages=[ChatMessage(role="user", content="finish this")],
            tools=[],
        )
    ]


async def test_effort_rejection_uses_same_model_preset_then_keeps_it_for_tool_rounds() -> None:
    provider = _Provider(["haiku", "smart"])
    provider.failures[("haiku", "high")] = REJECTION
    categories = ModelCategories(efficiency=[_preset("haiku"), _preset("smart", "low")])
    wrapper = CategoryFallbackProvider(provider, categories, 128_000)
    assert (await _call(wrapper))[0].content == "done"
    assert [(c["model"], c["reasoning_effort"]) for c in provider.calls] == [
        ("haiku", "high"),
        ("haiku", None),
    ]
    await _call(wrapper)
    assert provider.calls[-1]["reasoning_effort"] is None
    assert wrapper.effective_model == "haiku"


async def test_missing_model_uses_ordered_category_not_unrelated_catalog_entry() -> None:
    provider = _Provider(["unrelated", "replacement"])
    categories = ModelCategories(
        efficiency=[_preset("retired"), _preset("also-retired"), _preset("replacement", "low")],
        intelligence=[_preset("unrelated", "high")],
    )
    wrapper = CategoryFallbackProvider(provider, categories, 128_000)
    await _call(wrapper, "retired", "")
    assert [c["model"] for c in provider.calls] == ["replacement"]
    assert provider.calls[0]["reasoning_effort"] == "low"
    assert wrapper.effective_model == "replacement"


async def test_unreachable_catalog_still_recovers_from_actual_model_rejection() -> None:
    provider = _Provider()
    provider.failures[("retired", "")] = "model_not_found"
    wrapper = CategoryFallbackProvider(
        provider, ModelCategories(balanced=[_preset("retired"), _preset("replacement")]), 128_000
    )
    await _call(wrapper, "retired", "")
    assert [c["model"] for c in provider.calls] == ["retired", "replacement"]


async def test_all_rejected_presets_stop_with_clear_error() -> None:
    provider = _Provider(["haiku", "other"])
    provider.failures = {
        ("haiku", "high"): REJECTION,
        ("haiku", ""): "model_not_found",
        ("other", "low"): "invalid_context_tier",
    }
    wrapper = CategoryFallbackProvider(
        provider, ModelCategories(efficiency=[_preset("haiku"), _preset("other", "low")]), 128_000
    )
    with pytest.raises(LLMError, match="No working model preset remains"):
        await _call(wrapper)
    assert len(provider.calls) == 3


async def test_fallback_context_budget_drops_old_history_and_preserves_tools_and_options() -> None:
    provider = _Provider(["replacement"])
    wrapper = CategoryFallbackProvider(
        provider,
        ModelCategories(balanced=[_preset("retired"), _preset("replacement", tokens=1000)]),
        128_000,
    )
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="old " * 2000),
        ChatMessage(role="assistant", content="old answer"),
        ChatMessage(role="user", content="finish this"),
    ]
    tools = [SimpleNamespace(name="tool")]
    async for _ in wrapper.stream_chat_with_tools(
        model="retired",
        messages=messages,
        tools=tools,
        request_options={"temperature": 0.5},
    ):
        pass
    sent = provider.calls[0]
    assert messages[1] not in sent["messages"]
    assert sent["messages"][0] == messages[0]
    assert sent["messages"][-1] == messages[-1]
    assert sent["tools"] is tools
    assert sent["request_options"] == {"temperature": 0.5}
    assert len(messages) == 4


@pytest.mark.parametrize("partial,error", [(True, REJECTION), (False, "bad credentials")])
async def test_no_retry_after_output_or_on_auth_failure(partial: bool, error: str) -> None:
    provider = _Provider(["haiku", "smart"])
    provider.partial = partial
    provider.failures[("haiku", "high")] = error
    wrapper = CategoryFallbackProvider(
        provider, ModelCategories(efficiency=[_preset("haiku"), _preset("smart")]), 128_000
    )
    with pytest.raises(LLMError):
        await _call(wrapper)
    assert len(provider.calls) == 1


async def test_uncategorised_or_auto_request_is_not_changed() -> None:
    provider = _Provider(["haiku", "other", "auto"])
    provider.failures[("other", "high")] = REJECTION
    wrapper = CategoryFallbackProvider(
        provider, ModelCategories(efficiency=[_preset("haiku")]), 128_000
    )
    with pytest.raises(LLMError):
        await _call(wrapper, "other")
    await _call(wrapper, "auto", "")
    assert [c["model"] for c in provider.calls] == ["other", "auto"]


@pytest.fixture
async def stored_settings():
    with TestClient(create_app()):
        pass
    keys = (
        "model_fallbacks",
        "llm_model",
        "agents_default_model",
        "agents_reasoning_effort",
        "agents_context_tier",
    )
    async with SessionLocal() as s:
        originals = {
            key: row.value if (row := await s.get(AppSetting, key)) else None for key in keys
        }
    yield
    async with SessionLocal() as s:
        for key in originals:
            row = await s.get(AppSetting, key)
            if row is not None:
                await s.delete(row)
        await s.flush()
        for key, value in originals.items():
            if value is not None:
                s.add(AppSetting(key=key, value=value))
        await s.commit()


async def _store(key: str, value: Any) -> None:
    async with SessionLocal() as s:
        row = await s.get(AppSetting, key)
        if row is None:
            s.add(AppSetting(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)
        await s.commit()


async def test_settings_roundtrip_is_optional_and_preserves_provider_scopes(
    stored_settings,
) -> None:
    config = {
        "openai": {"efficiency": [{"model": "retired"}, {"model": "replacement"}]},
        "agents": {"intelligence": [{"model": "smart", "reasoning_effort": "high"}]},
    }
    with TestClient(create_app()) as client:
        response = client.put("/api/settings", json={"model_fallbacks": config})
        assert response.status_code == 200
        assert (
            response.json()["model_fallbacks"]["agents"]["intelligence"][0]["context_tier"]
            == "default"
        )
        assert (
            client.get("/api/settings").json()["model_fallbacks"]
            == response.json()["model_fallbacks"]
        )
        assert client.put("/api/settings", json={"model_fallbacks": {"bad": {}}}).status_code == 422
        assert (
            client.put("/api/settings", json={"model_fallbacks": {}}).json()["model_fallbacks"]
            == {}
        )


async def test_catalog_resolution_preserves_retired_categorised_pin(
    stored_settings, monkeypatch
) -> None:
    from precursor.backend.services.app_settings import resolve_llm_model

    monkeypatch.setattr(
        "precursor.backend.services.app_settings.offered_model_ids",
        AsyncMock(return_value=("unrelated", "replacement")),
    )
    await _store("llm_model", "retired")
    await _store(
        "model_fallbacks",
        {"github_copilot": {"balanced": [{"model": "retired"}, {"model": "replacement"}]}},
    )
    async with SessionLocal() as s:
        assert await resolve_llm_model(s) == "retired"


async def _agent(manager: AgentManager) -> tuple[AgentSession, AgentRun]:
    async with SessionLocal() as s:
        agent = AgentSession(
            title="Fallback test",
            task_prompt="finish this",
            model="haiku",
            use_mcp=False,
            use_skills=False,
            use_memory=False,
        )
        s.add(agent)
        await s.commit()
        await s.refresh(agent)
    run = await manager._open_run(agent.id)
    assert run is not None
    return agent, run


async def test_agent_catalog_uses_category_configuration_for_retired_pin(stored_settings) -> None:
    manager = AgentManager()
    manager.list_models = AsyncMock(return_value=[{"id": "unrelated"}, {"id": "replacement"}])
    await _store(
        "model_fallbacks",
        {
            "agents": {
                "efficiency": [
                    {"model": "haiku"},
                    {
                        "model": "replacement",
                        "reasoning_effort": "low",
                        "context_tier": "long_context",
                    },
                ],
            }
        },
    )
    assert await manager._model_selection.choices(1, "haiku", "high", "default") == [
        ("replacement", "low", "long_context"),
    ]


async def test_agent_creation_and_sync_keep_working_fallback_not_original_effort(
    stored_settings,
) -> None:
    manager = AgentManager()
    await _store("agents_reasoning_effort", "high")
    await _store("model_fallbacks", {"agents": {"efficiency": [{"model": "haiku"}]}})
    manager.list_models = AsyncMock(return_value=[{"id": "haiku"}])
    sdk = SimpleNamespace(on=lambda callback: None, set_model=AsyncMock())
    create = AsyncMock(side_effect=[LLMError(REJECTION), sdk])
    manager._client = SimpleNamespace(create_session=create)
    agent, run = await _agent(manager)
    live = await manager._ensure_live_locked(agent, run)
    assert create.await_count == 2
    assert create.call_args_list[0].kwargs["reasoning_effort"] == "high"
    assert "reasoning_effort" not in create.call_args_list[1].kwargs
    assert live.model_signature == ("haiku", None, "default")
    await manager._model_selection.sync_selected_model(agent, run)
    sdk.set_model.assert_not_awaited()
    assert run.model == "haiku"
    await _store("agents_reasoning_effort", "low")
    await manager._model_selection.sync_selected_model(agent, run)
    sdk.set_model.assert_awaited_once_with("haiku", reasoning_effort="low", context_tier="default")


async def test_agent_error_event_recovers_without_advancing_workflow_on_trailing_idle(
    stored_settings,
) -> None:
    manager = AgentManager()
    agent, run = await _agent(manager)
    sdk = SimpleNamespace(set_model=AsyncMock(), send=AsyncMock())
    live = _LiveSession(
        sdk_session=sdk,
        model_signature=("haiku", "high", "default"),
        model_candidates=[
            ("haiku", "high", "default"),
            ("haiku", None, "default"),
            ("replacement", "low", "long_context"),
        ],
        model_attempts={("haiku", "high", "default")},
        dispatched_prompt="full task with upstream context",
    )
    manager._live[run.id] = live
    await manager._patch_run(run.id, status="running", active_prompt="finish this")
    queued: list[Any] = []
    manager.enqueue = queued.append
    manager._advance_workflows = AsyncMock()

    class ErrorData:
        message = REJECTION

    class SessionIdleData:
        pass

    class AssistantTurnStartData:
        pass

    class AssistantMessageData:
        content = "done"

    await manager._handle_event_locked(run.id, ErrorData())
    assert len(queued) == 1
    assert (await manager._run(run.id)).status == "running"
    await manager._handle_event_locked(run.id, SessionIdleData())
    manager._advance_workflows.assert_not_called()
    await queued.pop()
    sdk.set_model.assert_awaited_once_with("haiku", reasoning_effort=None, context_tier="default")
    sdk.send.assert_awaited_once_with("full task with upstream context")
    await manager._handle_event_locked(run.id, SessionIdleData())
    assert (await manager._run(run.id)).status == "running"
    await manager._handle_event_locked(run.id, AssistantTurnStartData())
    await manager._handle_event_locked(run.id, AssistantMessageData())
    await manager._handle_event_locked(run.id, ErrorData())
    assert (await manager._run(run.id)).status == "failed"
    sdk.send.assert_awaited_once()
    for coroutine in queued:
        coroutine.close()
    assert live.model_output_started
    assert (await manager._load(agent.id)).model == "haiku"


async def test_agent_synchronous_send_is_bounded_and_clears_effort(stored_settings) -> None:
    manager = AgentManager()
    sdk = SimpleNamespace(
        set_model=AsyncMock(),
        send=AsyncMock(
            side_effect=[
                LLMError(REJECTION),
                None,
            ]
        ),
    )
    live = _LiveSession(
        sdk_session=sdk,
        model_signature=("haiku", "high", "default"),
        model_candidates=[("haiku", "high", "default"), ("haiku", None, "default")],
    )
    await manager._model_selection.send(-1, live, "finish this")
    assert sdk.send.await_count == 2
    sdk.set_model.assert_awaited_once_with("haiku", reasoning_effort=None, context_tier="default")
    sdk.send.side_effect = LLMError(REJECTION)
    with pytest.raises(LLMError):
        await manager._model_selection.send(-1, live, "finish this")
    assert sdk.send.await_count == 3


async def test_one_shot_reports_and_meters_actual_model(stored_settings, monkeypatch) -> None:
    from precursor.backend.services.llm import one_shot

    provider = _Provider(["replacement"])
    wrapper = CategoryFallbackProvider(
        provider, ModelCategories(balanced=[_preset("retired"), _preset("replacement")]), 128_000
    )
    monkeypatch.setattr(one_shot, "get_llm_provider", AsyncMock(return_value=wrapper))
    record = AsyncMock()
    monkeypatch.setattr(one_shot, "record_usage", record)
    async with SessionLocal() as s:
        result = await one_shot.complete_once(
            s,
            system="system",
            user="finish this",
            model="retired",
            usage_source="fallback-test",
        )
    assert result.model == "replacement"
    assert result.text == "done"
    assert record.call_args.kwargs["model"] == "replacement"


async def test_proxy_reports_actual_model_after_opening_fallback(stored_settings) -> None:
    from precursor.backend.services.openai_proxy import CompletionRequest, complete, open_completion

    provider = _Provider(["replacement"])
    wrapper = CategoryFallbackProvider(
        provider, ModelCategories(balanced=[_preset("retired"), _preset("replacement")]), 128_000
    )
    request = CompletionRequest(model="retired", messages=[ChatMessage(role="user", content="go")])
    result = await complete(await open_completion(wrapper, request), request)
    assert result["model"] == "replacement"


async def test_tool_loop_reports_actual_model_without_replaying_tools() -> None:
    from precursor.backend.services.turn_engine import AssistantFinalTurn, run_tool_loop

    provider = _Provider(["replacement"])
    wrapper = CategoryFallbackProvider(
        provider, ModelCategories(balanced=[_preset("retired"), _preset("replacement")]), 128_000
    )
    active = SimpleNamespace(tools=[], tool_to_server={})
    events = [
        e
        async for e in run_tool_loop(
            active=active,
            provider=wrapper,
            model="retired",
            reasoning_effort="",
            system_prompt="sys",
            history=[ChatMessage(role="user", content="go")],
            max_tool_rounds=2,
            max_input_tokens=128_000,
            max_tool_result_tokens=20_000,
        )
    ]
    assert isinstance(events[-1], AssistantFinalTurn)
    assert events[-1].model == "replacement"
    assert len(provider.calls) == 1


async def test_recovered_agent_completes_normally_and_advances_only_once(stored_settings) -> None:
    manager = AgentManager()
    agent, run = await _agent(manager)
    sdk = SimpleNamespace(set_model=AsyncMock(), send=AsyncMock())
    live = _LiveSession(
        sdk_session=sdk,
        model_signature=("haiku", "high", "default"),
        model_candidates=[("haiku", "high", "default"), ("replacement", None, "default")],
        model_attempts={("haiku", "high", "default")},
        dispatched_prompt="finish this",
    )
    manager._live[run.id] = live
    await manager._patch_run(run.id, status="running")
    queued: list[Any] = []
    manager.enqueue = queued.append
    manager._advance_workflows = AsyncMock()

    class ErrorData:
        message = REJECTION

    class AssistantTurnStartData:
        pass

    class AssistantMessageData:
        content = "finished"

    class SessionIdleData:
        pass

    await manager._handle_event_locked(run.id, ErrorData())
    await queued.pop()
    for event in (
        AssistantTurnStartData(),
        AssistantMessageData(),
        SessionIdleData(),
        SessionIdleData(),
    ):
        await manager._handle_event_locked(run.id, event)
    final = await manager._run(run.id)
    assert final.status == "idle"
    assert final.result_summary == "finished"
    assert final.error is None
    manager._advance_workflows.assert_called_once_with(run.id)
    for coroutine in queued:
        coroutine.close()
    assert (await manager._load(agent.id)).model == "haiku"


def test_http_auth_rate_limit_and_server_errors_do_not_switch_models() -> None:
    class StatusError(Exception):
        status_code = 429

    for status in (401, 403, 429, 500, 503):
        error = StatusError("model_not_available")
        error.status_code = status
        assert not is_model_rejection(error)
        wrapped = LLMError("model_not_available")
        wrapped.__cause__ = error
        assert not is_model_rejection(wrapped)


async def test_agent_repeated_preflight_rejections_try_each_preset_once(stored_settings) -> None:
    manager = AgentManager()
    _owner, run = await _agent(manager)
    sdk = SimpleNamespace(set_model=AsyncMock(), send=AsyncMock())
    live = _LiveSession(
        sdk_session=sdk,
        model_signature=("haiku", "high", "default"),
        model_candidates=[
            ("haiku", "high", "default"),
            ("haiku", None, "default"),
            ("replacement", None, "default"),
        ],
        model_attempts={("haiku", "high", "default")},
        dispatched_prompt="finish this",
    )
    manager._live[run.id] = live
    await manager._patch_run(run.id, status="running")
    queued: list[Any] = []
    manager.enqueue = queued.append

    class ErrorData:
        message = REJECTION

    await manager._handle_event_locked(run.id, ErrorData())
    # Duplicate reporting cannot fail the run while the first recovery is queued.
    await manager._handle_event_locked(run.id, ErrorData())
    assert len(queued) == 1
    await queued.pop()
    assert live.model_signature == ("haiku", None, "default")
    # A preflight refusal needn't emit turn-start before its error.
    await manager._handle_event_locked(run.id, ErrorData())
    await queued.pop()
    assert live.model_signature == ("replacement", None, "default")
    await manager._handle_event_locked(run.id, ErrorData())
    assert (await manager._run(run.id)).status == "failed"
    assert sdk.send.await_count == 2
    for coroutine in queued:
        coroutine.close()


async def test_cancel_during_model_switch_does_not_resend_task(stored_settings) -> None:
    manager = AgentManager()
    _owner, run = await _agent(manager)
    await manager._patch_run(run.id, status="running")

    async def cancel(*args, **kwargs):
        await manager._patch_run(run.id, status="cancelled")

    sdk = SimpleNamespace(set_model=AsyncMock(side_effect=cancel), send=AsyncMock())
    live = _LiveSession(
        sdk_session=sdk,
        model_signature=("haiku", "high", "default"),
        model_candidates=[("haiku", "high", "default"), ("replacement", None, "default")],
        model_attempts={("haiku", "high", "default")},
        dispatched_prompt="finish this",
        model_recovering=True,
        model_retry_scheduled=True,
    )
    manager._live[run.id] = live
    await manager._model_selection.recover(run.id, live, REJECTION)
    sdk.send.assert_not_awaited()
    assert (await manager._run(run.id)).status == "cancelled"
    assert not live.model_recovering
    assert not live.model_retry_scheduled


async def test_provider_factory_keeps_agent_and_provider_scopes_independent(
    stored_settings,
    monkeypatch,
) -> None:
    from dataclasses import replace

    from precursor.backend.services import llm

    provider = _Provider(["replacement"])
    monkeypatch.setattr(llm, "resolve_github_token", AsyncMock(return_value="test-only"))
    monkeypatch.setitem(
        llm.PROVIDERS,
        "github_copilot",
        replace(llm.PROVIDERS["github_copilot"], build=lambda config, token: provider),
    )
    await _store(
        "model_fallbacks",
        {
            "agents": {
                "efficiency": [{"model": "retired"}, {"model": "replacement"}],
            }
        },
    )
    async with SessionLocal() as s:
        assert await llm.get_llm_provider(s, override_provider="github_copilot") is provider
    await _store(
        "model_fallbacks",
        {
            "github_copilot": {
                "efficiency": [{"model": "retired"}, {"model": "replacement"}],
            }
        },
    )
    async with SessionLocal() as s:
        wrapper = await llm.get_llm_provider(s, override_provider="github_copilot")
    assert isinstance(wrapper, CategoryFallbackProvider)
    await _call(wrapper, "retired", "")
    assert [c["model"] for c in provider.calls] == ["replacement"]


async def test_queued_recovery_cannot_replay_a_newer_user_turn(stored_settings) -> None:
    manager = AgentManager()
    _owner, run = await _agent(manager)
    await manager._patch_run(run.id, status="running")
    sdk = SimpleNamespace(set_model=AsyncMock(), send=AsyncMock())
    live = _LiveSession(
        sdk_session=sdk,
        model_turn_id=2,
        dispatched_prompt="a newer user request",
        model_candidates=[("replacement", None, "default")],
    )
    manager._live[run.id] = live
    await manager._model_selection.recover(run.id, live, REJECTION, turn_id=1)
    sdk.set_model.assert_not_awaited()
    sdk.send.assert_not_awaited()
    assert (await manager._run(run.id)).status == "running"


async def test_catalog_api_reports_actual_source_not_requested_provider(
    stored_settings, monkeypatch
) -> None:
    from precursor.backend.routers import llm as llm_router
    from precursor.backend.services.llm.mock import MockProvider

    monkeypatch.setattr(
        llm_router, "get_llm_provider", AsyncMock(return_value=_Provider(["model-a"]))
    )
    with TestClient(create_app()) as client:
        response = client.get("/api/llm/models?provider=github_copilot")
        assert response.status_code == 200
        assert response.json()[0]["catalog_provider"] == "openai"
        monkeypatch.setattr(llm_router, "get_llm_provider", AsyncMock(return_value=MockProvider()))
        response = client.get("/api/llm/models?provider=openai")
        assert response.status_code == 200
        assert response.json()
        assert all(model["catalog_provider"] == "mock" for model in response.json())
