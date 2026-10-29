from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from precursor.backend.routers import llm as llm_router
from precursor.backend.services.llm import azure_foundry
from precursor.backend.services.llm.base import TextDeltaEvent
from precursor.backend.services.llm.registry import PROVIDERS


@pytest.mark.parametrize(
    ("deployment", "expected"),
    [
        ("", []),
        ("legacy-deployment", ["legacy-deployment"]),
        (" fast, smart , fast,,\n balanced ", ["fast", "smart", "balanced"]),
        (" , \n ", []),
    ],
)
async def test_configured_deployments(
    deployment: str, expected: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(azure_foundry, "AsyncAzureOpenAI", lambda **_: SimpleNamespace())
    provider = PROVIDERS["azure_foundry"].build(
        {"endpoint": "https://example.openai.azure.com", "key": "test", "deployment": deployment},
        "",
    )
    models = await provider.list_models()
    assert [model.id for model in models] == expected
    assert [model.name for model in models] == expected
    assert all(model.publisher == "Azure" for model in models)
    assert not PROVIDERS["azure_foundry"].discovers_models


async def test_model_catalog_api_exposes_each_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(azure_foundry, "AsyncAzureOpenAI", lambda **_: SimpleNamespace())
    provider = PROVIDERS["azure_foundry"].build(
        {
            "endpoint": "https://example.openai.azure.com",
            "key": "test",
            "deployment": "fast, smart",
        },
        "",
    )
    monkeypatch.setattr(llm_router, "get_llm_provider", AsyncMock(return_value=provider))
    models = await llm_router.list_models(provider="azure_foundry", session=SimpleNamespace())
    assert [model.id for model in models] == ["fast", "smart"]
    assert all(model.catalog_provider == "azure_foundry" for model in models)
    monkeypatch.setattr(llm_router, "resolve_mock_provider_enabled", AsyncMock(return_value=True))
    specs = await llm_router.list_providers(session=SimpleNamespace())
    spec = next(spec for spec in specs if spec.id == "azure_foundry")
    deployment = next(field for field in spec.fields if field.name == "deployment")
    assert deployment.label == "Deployment names"
    assert "Apply & refresh models" in deployment.help


@pytest.mark.parametrize(
    ("model", "expected"), [("", "fast"), ("smart", "smart"), ("manual", "manual")]
)
async def test_plain_chat_routes_to_selected_deployment(
    model: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def chunks() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="reply"))])

    create = AsyncMock(return_value=chunks())
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(azure_foundry, "AsyncAzureOpenAI", lambda **_: client)
    provider = azure_foundry.AzureFoundryProvider(
        endpoint="https://example.openai.azure.com", api_key="test", deployment=" fast, smart "
    )
    assert [chunk async for chunk in provider.stream_chat(model=model, messages=[])] == ["reply"]
    assert create.call_args.kwargs["model"] == expected


@pytest.mark.parametrize(
    ("model", "expected"), [("", "fast"), ("smart", "smart"), ("manual", "manual")]
)
async def test_tool_chat_routes_to_selected_deployment(
    model: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = SimpleNamespace()
    monkeypatch.setattr(azure_foundry, "AsyncAzureOpenAI", lambda **_: client)
    calls: list[dict[str, object]] = []

    async def stream(**kwargs: object) -> AsyncIterator[TextDeltaEvent]:
        calls.append(kwargs)
        yield TextDeltaEvent(content="reply")

    monkeypatch.setattr(azure_foundry, "stream_openai_tools", stream)
    provider = azure_foundry.AzureFoundryProvider(
        endpoint="https://example.openai.azure.com", api_key="test", deployment=" fast, smart "
    )
    events = [
        event
        async for event in provider.stream_chat_with_tools(
            model=model,
            messages=[],
            tools=[],
            reasoning_effort="low",
            request_options={"max_tokens": 123},
        )
    ]
    assert events == [TextDeltaEvent(content="reply")]
    assert calls == [
        {
            "client": client,
            "model": expected,
            "messages": [],
            "tools": [],
            "reasoning_effort": "low",
            "request_options": {"max_tokens": 123},
        }
    ]
