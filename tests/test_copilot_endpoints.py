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

from precursor.backend.services.llm import github_copilot
from precursor.backend.services.llm._openai_compat import (
    UnsupportedEndpointError,
    _friendly_request_error,
)
from precursor.backend.services.llm._responses_compat import (
    to_responses_input,
    to_responses_tools,
)
from precursor.backend.services.llm.base import ChatMessage, LLMError, ToolDef
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


def test_integrator_denial_explains_it_is_not_configurable() -> None:
    exc = _friendly_request_error(
        _status_error("model_not_available_for_integrator", "not available for integrator"),
        tool_count=0,
    )
    assert isinstance(exc, LLMError)
    assert not isinstance(exc, UnsupportedEndpointError)
    # This one can't be fixed by the user fiddling with settings, so the message
    # must send them to a different model rather than to a config screen.
    assert "different model" in str(exc)


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
