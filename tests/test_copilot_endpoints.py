"""Endpoint routing for the GitHub Copilot provider.

Copilot splits its catalogue across two API surfaces — ``/chat/completions`` and
the Responses API — and a model served by one is rejected by the other. These
tests pin down the catalogue filtering, the routing decision, and the Responses
wire-format translation, since getting any of them wrong makes a model that the
picker offers fail on first use.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import APIStatusError

from precursor.backend.services.llm import _openai_compat, github_copilot
from precursor.backend.services.llm._openai_compat import (
    ModelNotEntitledError,
    UnsupportedEndpointError,
    _friendly_request_error,
    open_stream_with_retry,
)
from precursor.backend.services.llm._responses_compat import (
    to_responses_input,
    to_responses_tools,
)
from precursor.backend.services.llm.base import ChatMessage, ToolDef, TurnDoneEvent
from precursor.backend.services.llm.github_copilot import (
    GitHubCopilotProvider,
    _prefers_responses,
)


@pytest.fixture(autouse=True)
def _clear_endpoint_map() -> Any:
    """The learned endpoint map is module-level; don't leak it between tests."""
    github_copilot._MODEL_ENDPOINTS.clear()
    yield
    github_copilot._MODEL_ENDPOINTS.clear()


def _model(mid: str, endpoints: list[str] | None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": mid,
        "name": mid,
        "vendor": "test",
        "model_picker_enabled": True,
        "capabilities": {"type": "chat"},
    }
    if endpoints is not None:
        item["supported_endpoints"] = endpoints
    return item


def _stub_catalogue(monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]) -> None:
    """Serve ``items`` as the /models payload without touching the network."""

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"data": items}

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, *args: Any, **kwargs: Any) -> _Response:
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


async def test_list_models_drops_models_no_endpoint_can_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_catalogue(
        monkeypatch,
        [
            _model("chat-only", ["/chat/completions"]),
            _model("responses-only", ["/responses"]),
            _model("anthropic-native", ["/v1/messages"]),
        ],
    )
    models = await GitHubCopilotProvider(token="t").list_models()

    # A model we can't drive is worse than absent: offering it guarantees a
    # failure the moment the user picks it.
    assert sorted(m.id for m in models) == ["chat-only", "responses-only"]


async def test_list_models_keeps_entries_without_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_catalogue(monkeypatch, [_model("legacy", None)])
    models = await GitHubCopilotProvider(token="t").list_models()

    assert [m.id for m in models] == ["legacy"]
    # Unrecorded means "assume chat-completions", not "unusable".
    assert not _prefers_responses("legacy")


async def test_catalogue_teaches_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_catalogue(
        monkeypatch,
        [
            _model("both", ["/chat/completions", "/responses"]),
            _model("responses-only", ["/responses"]),
            _model("chat-only", ["/chat/completions"]),
        ],
    )
    await GitHubCopilotProvider(token="t").list_models()

    assert _prefers_responses("responses-only")
    # Prefer the well-trodden endpoint whenever the model serves it.
    assert not _prefers_responses("both")
    assert not _prefers_responses("chat-only")
    # Unknown models stay optimistic and get corrected by the error path.
    assert not _prefers_responses("never-seen")


def _status_error(code: str, message: str) -> APIStatusError:
    request = httpx.Request("POST", "https://api.githubcopilot.com/chat/completions")
    response = httpx.Response(400, request=request)
    return APIStatusError(
        message, response=response, body={"error": {"code": code, "message": message}}
    )


def test_wrong_endpoint_is_typed_so_the_provider_can_retry() -> None:
    exc = _friendly_request_error(
        _status_error("unsupported_api_for_model", 'model "x" is not accessible'),
        tool_count=0,
    )
    # Typed rather than generic: the provider catches it to retry on Responses.
    assert isinstance(exc, UnsupportedEndpointError)


def test_integrator_denial_is_typed_as_transient() -> None:
    exc = _friendly_request_error(
        _status_error("model_not_available_for_integrator", "not available for integrator"),
        tool_count=0,
    )
    assert isinstance(exc, ModelNotEntitledError)
    assert not isinstance(exc, UnsupportedEndpointError)
    # The rejection flaps rather than sticking, so the message must invite a
    # retry — telling the user the model is unavailable would be wrong.
    assert "intermittent" in str(exc)
    assert "different model" in str(exc)


async def test_flaky_refusal_is_retried_until_it_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_openai_compat, "ENTITLEMENT_RETRY_BACKOFF", 0)
    attempts = 0

    async def _create() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _status_error("model_not_available_for_integrator", "nope")
        return "stream"

    # Copilot serves a model from only part of its fleet, so the same request
    # alternates between 200 and 400. Riding that out is the whole point.
    assert await open_stream_with_retry(_create, tool_count=0) == "stream"
    assert attempts == 3


async def test_persistent_refusal_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_openai_compat, "ENTITLEMENT_RETRY_BACKOFF", 0)
    attempts = 0

    async def _create() -> str:
        nonlocal attempts
        attempts += 1
        raise _status_error("model_not_available_for_integrator", "nope")

    with pytest.raises(ModelNotEntitledError):
        await open_stream_with_retry(_create, tool_count=0)
    # Bounded: the user shouldn't wait on a model that's genuinely out of reach.
    assert attempts == _openai_compat.ENTITLEMENT_RETRY_ATTEMPTS


async def test_actionable_errors_are_not_retried() -> None:
    attempts = 0

    async def _create() -> str:
        nonlocal attempts
        attempts += 1
        raise _status_error("unsupported_api_for_model", "wrong surface")

    with pytest.raises(UnsupportedEndpointError):
        await open_stream_with_retry(_create, tool_count=0)
    # Retrying a verdict that won't change just delays the message.
    assert attempts == 1


def _fake_stream(events: list[Any], *, raises: Exception | None = None) -> Any:
    async def _gen(**kwargs: Any) -> Any:
        if raises is not None:
            raise raises
        for event in events:
            yield event

    return _gen


async def test_unknown_model_refused_on_chat_completions_tries_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = TurnDoneEvent(finish_reason="stop")
    monkeypatch.setattr(
        github_copilot,
        "stream_openai_tools",
        _fake_stream([], raises=ModelNotEntitledError("refused")),
    )
    monkeypatch.setattr(github_copilot, "stream_responses_tools", _fake_stream([done]))

    events = [
        e
        async for e in GitHubCopilotProvider(token="t").stream_chat_with_tools(
            model="never-seen", messages=[ChatMessage(role="user", content="hi")], tools=()
        )
    ]

    # Copilot answers this — not ``unsupported_api_for_model`` — for some
    # Responses-only models, so a model we only guessed about gets a second try.
    assert events == [done]
    assert _prefers_responses("never-seen")


async def test_known_chat_model_gets_no_second_endpoint_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_copilot._MODEL_ENDPOINTS["chat-only"] = frozenset({"/chat/completions"})
    monkeypatch.setattr(
        github_copilot,
        "stream_openai_tools",
        _fake_stream([], raises=ModelNotEntitledError("refused")),
    )

    def _unreachable(**kwargs: Any) -> Any:
        raise AssertionError("must not retry a model the catalogue placed on chat-completions")

    monkeypatch.setattr(github_copilot, "stream_responses_tools", _unreachable)

    with pytest.raises(ModelNotEntitledError):
        async for _ in GitHubCopilotProvider(token="t").stream_chat_with_tools(
            model="chat-only", messages=[ChatMessage(role="user", content="hi")], tools=()
        ):
            pass


def test_tool_exchange_survives_translation_to_responses_items() -> None:
    items = to_responses_input(
        [
            ChatMessage(role="system", content="Be terse."),
            ChatMessage(role="user", content="weather in Paris?"),
            ChatMessage(
                role="assistant",
                content="Checking.",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                    }
                ],
            ),
            ChatMessage(role="tool", content="18C", tool_call_id="call_1", name="get_weather"),
        ]
    )

    assert items[0] == {"role": "system", "content": [{"type": "input_text", "text": "Be terse."}]}
    assert items[1]["role"] == "user"
    # The assistant turn splits: its prose, then the call as a sibling item.
    assert items[2] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Checking."}],
    }
    assert items[3] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "get_weather",
        "arguments": '{"city":"Paris"}',
    }
    # The result correlates by call_id — losing it strands the model mid-turn.
    assert items[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "18C",
    }


def test_images_ride_along_as_input_parts() -> None:
    items = to_responses_input(
        [ChatMessage(role="user", content="what is this?", image_urls=["data:image/png;base64,x"])]
    )

    assert items[0]["content"] == [
        {"type": "input_text", "text": "what is this?"},
        {"type": "input_image", "image_url": "data:image/png;base64,x"},
    ]


def test_empty_turn_keeps_its_slot() -> None:
    # An empty content array is rejected outright, which would drop the turn.
    items = to_responses_input([ChatMessage(role="user", content="")])

    assert items == [{"role": "user", "content": [{"type": "input_text", "text": ""}]}]


def test_tool_schemas_are_flattened() -> None:
    tools = to_responses_tools(
        [ToolDef(name="get_weather", description="Get weather", parameters={"type": "object"})]
    )

    # Responses drops the nested "function" wrapper chat-completions requires.
    assert tools == [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object"},
        }
    ]
