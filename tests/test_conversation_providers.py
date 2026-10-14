"""Chat/topic selections stay isolated across providers and model calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting, Chat, Topic
from precursor.backend.services import model_catalog
from precursor.backend.services.conversation_llm import saved_selection
from precursor.backend.services.conversation_turn import resolve_turn_settings
from precursor.backend.services.llm import ProviderConfigurationError, get_llm_provider
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMModel,
    ProviderEvent,
    TextDeltaEvent,
    ToolDef,
    TurnDoneEvent,
)
from precursor.backend.services.llm.mock import MockProvider
from precursor.backend.services.llm.one_shot import complete_once
from precursor.backend.services.llm.registry import PROVIDERS
from precursor.backend.services.model_fallbacks import resolve_llm_preparation_budget
from precursor.backend.services.turn import run_topic_turn


class RecordingProvider(MockProvider):
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, str | None]] = []
        self.catalog_calls = 0

    async def list_models(self) -> list[LLMModel]:
        self.catalog_calls += 1
        return [
            LLMModel(
                id="shared-model",
                name=self.name,
                context_window=128_000,
                supported_reasoning_efforts=["low", "high"],
            ),
            LLMModel(id="alternative", name="Alternative"),
        ]

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
        request_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.calls.append((model, reasoning_effort))
        yield TextDeltaEvent(content=f"{self.name}:{model}:{reasoning_effort}")
        yield TurnDoneEvent(finish_reason="stop")


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> dict[str, RecordingProvider]:
    result = {name: RecordingProvider(name) for name in ("github_copilot", "azure_foundry")}
    for name, provider in result.items():
        monkeypatch.setitem(
            PROVIDERS, name, replace(PROVIDERS[name], build=lambda _cfg, _tok, p=provider: p)
        )
    monkeypatch.setattr(
        "precursor.backend.services.llm.resolve_github_token", AsyncMock(return_value="fake")
    )
    monkeypatch.setattr(
        "precursor.backend.services.conversation_turn.load_enabled_mcp_servers",
        AsyncMock(return_value=[]),
    )
    model_catalog.invalidate_model_catalog()
    yield result
    model_catalog.invalidate_model_catalog()


@pytest.fixture
def client(providers: dict[str, RecordingProvider]) -> Iterator[TestClient]:
    keys = (
        "llm_provider",
        "llm_model",
        "llm_model_category",
        "llm_providers",
        "model_fallbacks",
        "llm_reasoning_effort",
        "llm_max_input_tokens",
        "mock_provider_enabled",
    )

    async def snapshot() -> dict[str, str | None]:
        async with SessionLocal() as session:
            return {
                key: row.value if (row := await session.get(AppSetting, key)) else None
                for key in keys
            }

    with TestClient(create_app()) as test_client:
        before = anyio.run(snapshot)
        response = test_client.put(
            "/api/settings",
            json={
                "llm_provider": "github_copilot",
                "llm_model": "shared-model",
                "llm_model_category": None,
                "model_fallbacks": {},
                "llm_reasoning_effort": "low",
                "llm_max_input_tokens": 128_000,
                "llm_providers": {
                    "azure_foundry": {
                        "endpoint": "https://example.openai.azure.com",
                        "key": "fake",
                        "deployment": "shared-model, alternative",
                    }
                },
            },
        )
        assert response.status_code == 200, response.text
        yield test_client
        for route in ("chats", "topics"):
            for item in test_client.get(f"/api/{route}").json():
                if item["title"].startswith("Multi-provider"):
                    test_client.delete(f"/api/{route}/{item['id']}")

    async def restore() -> None:
        async with SessionLocal() as session:
            for key, value in before.items():
                row = await session.get(AppSetting, key)
                if value is None:
                    if row:
                        await session.delete(row)
                elif row:
                    row.value = value
                else:
                    session.add(AppSetting(key=key, value=value))
            await session.commit()

    anyio.run(restore)


def selection(provider: str = "azure_foundry", **changes: Any) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": "shared-model",
        "category": None,
        "reasoning_effort": "high",
        "context_tokens": 32_000,
        **changes,
    }


@pytest.mark.parametrize("route", ["chats", "topics"])
def test_selection_roundtrip_reset_and_omitted_update(client: TestClient, route: str) -> None:
    response = client.post(f"/api/{route}", json={"title": "Multi-provider roundtrip"})
    assert response.status_code == 201
    item = response.json()
    path = f"/api/{route}/{item['id']}"
    assert item["llm_selection"] is None
    assert (
        client.patch(path, json={"llm_selection": selection()}).json()["llm_selection"]
        == selection()
    )
    assert client.get(path).json()["llm_selection"] == selection()
    assert client.patch(path, json={"pinned": True}).json()["llm_selection"] == selection()
    assert client.patch(path, json={"llm_selection": None}).json()["llm_selection"] is None
    assert client.get("/api/settings").json()["llm_provider"] == "github_copilot"


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "unknown"},
        {"model": ""},
        {"category": "balanced"},
        {"context_tokens": 0},
        {"context_tokens": 5_000_001},
        {"reasoning_effort": "unsupported"},
        {"secret": "never-store"},
        {"model": "", "category": "balanced"},
    ],
)
def test_invalid_selections_are_rejected(client: TestClient, changes: dict[str, Any]) -> None:
    response = client.post(
        "/api/chats",
        json={
            "title": "Multi-provider invalid",
            "llm_selection": selection(**changes),
        },
    )
    assert response.status_code == 422, response.text


def test_side_chat_and_promotion_preserve_override(client: TestClient) -> None:
    topic = client.post(
        "/api/topics",
        json={
            "title": "Multi-provider source",
            "llm_selection": selection(),
        },
    ).json()
    chat = client.post(f"/api/topics/{topic['id']}/chats", json={}).json()
    assert chat["llm_selection"] == selection()
    client.patch(f"/api/topics/{topic['id']}", json={"llm_selection": None})
    assert client.get(f"/api/chats/{chat['id']}").json()["llm_selection"] == selection()
    promoted = client.post(f"/api/chats/{chat['id']}/promote").json()
    assert promoted["llm_selection"] == selection()
    client.delete(f"/api/topics/{promoted['id']}")


async def test_concurrent_same_model_ids_use_distinct_providers(
    client: TestClient,
    providers: dict[str, RecordingProvider],
) -> None:
    chat = client.post(
        "/api/chats",
        json={
            "title": "Multi-provider Azure",
            "llm_selection": selection(),
        },
    ).json()
    topic = client.post(
        "/api/topics",
        json={
            "title": "Multi-provider GitHub",
            "llm_selection": selection("github_copilot"),
        },
    ).json()

    async def resolve(**ids: int) -> tuple[str, str, int]:
        async with SessionLocal() as session:
            settings = await resolve_turn_settings(session, **ids)
            return settings.provider.name, settings.model, settings.max_input_tokens

    actual = await asyncio.gather(resolve(chat_id=chat["id"]), resolve(topic_id=topic["id"]))
    assert actual == [
        ("azure_foundry", "shared-model", 32_000),
        ("github_copilot", "shared-model", 32_000),
    ]
    assert all(provider.catalog_calls == 1 for provider in providers.values())
    async with SessionLocal() as session:
        overridden = await resolve_turn_settings(
            session, chat_id=chat["id"], model_override="alternative"
        )
    assert overridden.provider.name == "azure_foundry"
    assert overridden.model == "alternative"


@pytest.mark.parametrize("route", ["chats", "topics"])
def test_stream_and_retry_use_conversation_provider(
    client: TestClient,
    providers: dict[str, RecordingProvider],
    route: str,
) -> None:
    item = client.post(
        f"/api/{route}",
        json={
            "title": "Multi-provider stream",
            "llm_selection": selection(),
        },
    ).json()
    path = f"/api/{route}/{item['id']}/messages"
    response = client.post(path + "/stream", json={"content": "hello"})
    assert response.status_code == 200, response.text
    assert "azure_foundry:shared-model:high" in response.text
    messages = client.get(path).json()
    prompt = next(message for message in messages if message["role"] == "user")
    retry = client.post(
        path + "/stream",
        json={
            "content": "hello",
            "retry_message_id": prompt["id"],
            "model": "alternative",
        },
    )
    assert retry.status_code == 200, retry.text
    assert providers["azure_foundry"].calls == [
        ("shared-model", "high"),
        ("alternative", "high"),
    ]
    assert not providers["github_copilot"].calls


async def test_one_shots_and_preparation_budget_follow_selection(client: TestClient) -> None:
    chat = client.post(
        "/api/chats",
        json={
            "title": "Multi-provider one shot",
            "llm_selection": selection(),
        },
    ).json()
    async with SessionLocal() as session:
        result = await complete_once(
            session,
            system="s",
            user="u",
            usage_source="test",
            chat_id=chat["id"],
            model="unrelated-feature-model",
            reasoning_effort="low",
        )
        budget = await resolve_llm_preparation_budget(session, chat_id=chat["id"])
        global_result = await complete_once(
            session,
            system="s",
            user="u",
            usage_source="test",
            chat_id=chat["id"],
            use_conversation_selection=False,
        )
    assert result.text == "azure_foundry:shared-model:high"
    assert budget == 32_000
    assert global_result.text == "github_copilot:shared-model:None"


async def test_scheduled_topic_turn_uses_saved_provider(
    client: TestClient,
    providers: dict[str, RecordingProvider],
) -> None:
    topic = client.post(
        "/api/topics",
        json={
            "title": "Multi-provider scheduled",
            "llm_selection": selection(),
        },
    ).json()
    await run_topic_turn(topic["id"], "scheduled")
    assert providers["azure_foundry"].calls == [("shared-model", "high")]
    assert not providers["github_copilot"].calls


def test_unconfigured_selection_and_catalogue_fail_without_mock(
    client: TestClient,
    providers: dict[str, RecordingProvider],
) -> None:
    client.put("/api/settings", json={"llm_providers": {"azure_foundry": {"key": ""}}})
    chat = client.post(
        "/api/chats",
        json={
            "title": "Multi-provider unconfigured",
            "llm_selection": selection(),
        },
    ).json()
    response = client.post(f"/api/chats/{chat['id']}/messages/stream", json={"content": "hello"})
    assert response.status_code == 422
    assert "Azure AI Foundry is not configured" in response.json()["detail"]
    assert client.get("/api/llm/models?provider=azure_foundry").status_code == 422
    assert client.get("/api/llm/models?provider=unknown").status_code == 422
    assert not providers["azure_foundry"].calls


def test_ollama_requires_a_saved_url(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = RecordingProvider("ollama")
    monkeypatch.setitem(
        PROVIDERS, "ollama", replace(PROVIDERS["ollama"], build=lambda _cfg, _tok: provider)
    )
    spec = next(item for item in client.get("/api/llm/providers").json() if item["id"] == "ollama")
    assert next(field for field in spec["fields"] if field["name"] == "base_url")["required"]
    assert client.get("/api/llm/models?provider=ollama").status_code == 422
    client.put(
        "/api/settings",
        json={"llm_providers": {"ollama": {"base_url": "http://localhost:11434/v1"}}},
    )
    assert client.get("/api/llm/models?provider=ollama").status_code == 200
    chat = client.post(
        "/api/chats",
        json={"title": "Multi-provider Ollama", "llm_selection": selection("ollama")},
    ).json()
    response = client.post(f"/api/chats/{chat['id']}/messages/stream", json={"content": "hello"})
    assert "ollama:shared-model:high" in response.text


async def test_inherited_global_offline_behavior_is_preserved(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "precursor.backend.services.llm.resolve_github_token", AsyncMock(return_value="")
    )
    async with SessionLocal() as session:
        assert isinstance(await get_llm_provider(session), MockProvider)
        with pytest.raises(ProviderConfigurationError, match="not configured"):
            await get_llm_provider(session, override_provider="github_copilot", strict=True)


async def test_provider_specific_category_budget_and_reset(client: TestClient) -> None:
    presets = {
        "categories": [
            {
                "id": "balanced",
                "label": "Balanced",
                "presets": [
                    {
                        "provider": "azure_foundry",
                        "model": "shared-model",
                        "reasoning_effort": "low",
                        "context_tokens": 64_000,
                    },
                    {
                        "provider": "azure_foundry",
                        "model": "alternative",
                        "reasoning_effort": "high",
                        "context_tokens": 128_000,
                    },
                ],
            }
        ]
    }
    assert (
        client.put(
            "/api/settings",
            json={
                "model_fallbacks": presets,
            },
        ).status_code
        == 200
    )
    topic = client.post(
        "/api/topics",
        json={
            "title": "Multi-provider category",
            "llm_selection": selection(model="", category="balanced"),
        },
    ).json()
    async with SessionLocal() as session:
        resolved = await resolve_turn_settings(session, topic_id=topic["id"])
        assert resolved.provider.name == "azure_foundry"
        assert resolved.max_input_tokens == 128_000
        assert await resolve_llm_preparation_budget(session, topic_id=topic["id"]) == 128_000
    client.patch(f"/api/topics/{topic['id']}", json={"llm_selection": None})
    async with SessionLocal() as session:
        assert await saved_selection(session, topic_id=topic["id"]) is None
        inherited = await resolve_turn_settings(session, topic_id=topic["id"])
        assert inherited.provider.name == "github_copilot"


async def test_nullable_selection_columns_are_migrated(client: TestClient) -> None:
    async with SessionLocal() as session:
        chat = Chat(title="Multi-provider migration", slug="multi-provider-migration")
        topic = Topic(title="Multi-provider migration", slug="multi-provider-migration")
        session.add_all([chat, topic])
        await session.commit()
        await session.refresh(chat)
        await session.refresh(topic)
        assert chat.llm_selection is None
        assert topic.llm_selection is None


def test_mock_provider_can_be_disabled(client: TestClient) -> None:
    ids = {entry["id"] for entry in client.get("/api/llm/providers").json()}
    assert "mock" in ids
    assert client.get("/api/settings").json()["mock_provider_enabled"] is True

    assert client.put("/api/settings", json={"mock_provider_enabled": False}).status_code == 200
    ids = {entry["id"] for entry in client.get("/api/llm/providers").json()}
    assert "mock" not in ids
    assert "mock" in {
        entry["id"] for entry in client.get("/api/llm/providers?include_disabled=true").json()
    }

    async def unconfigured() -> None:
        async with SessionLocal() as session:
            await get_llm_provider(session, override_provider="openai")

    with pytest.raises(ProviderConfigurationError):
        asyncio.run(unconfigured())
