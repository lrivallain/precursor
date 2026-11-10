"""Azure Speech tokens: subscription key or the Azure CLI sign-in."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.routers import stt as stt_router
from precursor.backend.services import stt
from precursor.backend.services.azure_cli import ARM_RESOURCE, COGNITIVE_SERVICES_RESOURCE
from precursor.backend.services.speech_settings import SpeechConfig

RESOURCE_ID = (
    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/my-speech"
)
ENDPOINT = "https://my-speech.cognitiveservices.azure.com/"


class _FakeTokens:
    """Stands in for the CLI: one token per (tenant, resource), and a call log."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def provider(self, tenant: str, resource: str) -> Callable[[], Any]:
        async def get() -> str:
            self.calls.append((tenant, resource))
            return "arm-token" if resource == ARM_RESOURCE else "speech-token"

        return get


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeTokens]:
    fake = _FakeTokens()
    monkeypatch.setattr(stt, "_token_provider", fake.provider)
    stt._RESOURCE_IDS.clear()
    yield fake
    stt._RESOURCE_IDS.clear()


def _graph(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]):
    real = httpx.AsyncClient
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        stt.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(record), **kw),
    )
    return seen


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (ENDPOINT, "my-speech"),
        ("https://My-Speech.cognitiveservices.azure.com", "my-speech"),
        ("https://foundry-1.services.ai.azure.com/", "foundry-1"),
    ],
)
def test_custom_subdomain(endpoint: str, expected: str) -> None:
    assert stt.custom_subdomain(endpoint) == expected


@pytest.mark.parametrize("endpoint", ["", "https://westeurope.api.cognitive.microsoft.com/"])
def test_regional_or_missing_endpoint_is_refused(endpoint: str) -> None:
    with pytest.raises(stt.SpeechAuthError):
        stt.custom_subdomain(endpoint)


async def test_configured_resource_id_skips_discovery(tokens: _FakeTokens) -> None:
    token = await stt.mint_entra_speech_token(ENDPOINT, tenant="contoso", resource_id=RESOURCE_ID)
    assert token == f"aad#{RESOURCE_ID}#speech-token"
    assert tokens.calls == [("contoso", COGNITIVE_SERVICES_RESOURCE)]


async def test_resource_id_is_discovered_once(
    tokens: _FakeTokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _graph(monkeypatch, lambda _r: httpx.Response(200, json={"data": [{"id": RESOURCE_ID}]}))
    for _ in range(2):
        assert await stt.mint_entra_speech_token(ENDPOINT) == f"aad#{RESOURCE_ID}#speech-token"
    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == "Bearer arm-token"
    assert "customSubDomainName =~ 'my-speech'" in json.loads(seen[0].content)["query"]
    assert ("", ARM_RESOURCE) in tokens.calls


async def test_no_matching_resource_names_the_tenant(
    tokens: _FakeTokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    _graph(monkeypatch, lambda _r: httpx.Response(200, json={"data": []}))
    with pytest.raises(stt.SpeechAuthError, match="tenant contoso"):
        await stt.mint_entra_speech_token(ENDPOINT, tenant="contoso")


async def test_bad_resource_id_or_tenant_is_refused(tokens: _FakeTokens) -> None:
    with pytest.raises(stt.SpeechAuthError, match="resource ID"):
        await stt.mint_entra_speech_token(ENDPOINT, resource_id="my-speech")
    with pytest.raises(stt.SpeechAuthError, match="tenant"):
        await stt.mint_entra_speech_token(ENDPOINT, tenant="--foo")
    assert tokens.calls == []


def _config(**overrides: Any) -> SpeechConfig:
    values: dict[str, Any] = {
        "endpoint": ENDPOINT,
        "auth": "azure_cli",
        "key": "",
        "tenant": "",
        "resource_id": RESOURCE_ID,
    }
    return SpeechConfig(**{**values, **overrides})


def test_cli_mode_is_ready_without_a_key() -> None:
    assert _config().ready
    assert not _config(auth="key").ready
    assert _config(auth="key", key="k").ready
    assert not _config(endpoint="").ready


def test_token_route_uses_the_cli_sign_in(
    tokens: _FakeTokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def config(_session: Any) -> SpeechConfig:
        return _config(tenant="contoso")

    monkeypatch.setattr(stt_router, "resolve_speech_config", config)
    with TestClient(create_app()) as client:
        body = client.get("/api/stt/token").json()
        assert body["token"] == f"aad#{RESOURCE_ID}#speech-token"
        assert body["endpoint"] == ENDPOINT

        test = client.post(
            "/api/stt/test",
            json={"endpoint": "https://westeurope.api.cognitive.microsoft.com/"},
        ).json()
        assert test["ok"] is False
        assert "custom-domain" in test["detail"]

        test = client.post("/api/stt/test", json={"endpoint": ENDPOINT, "auth": "key"}).json()
        assert test == {"ok": False, "detail": "Provide the subscription key."}
