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
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="reply", tool_calls=None), finish_reason=None
                )
            ],
            usage=None,
        )

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


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._out = (stdout, stderr)
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out


def _az_output(token: str = "tok", expires_on: float | None = None) -> bytes:
    import json
    import time

    return json.dumps(
        {"accessToken": token, "expires_on": expires_on or time.time() + 3600}
    ).encode()


async def test_cli_token_uses_default_tenant_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    exec_mock = AsyncMock(return_value=_FakeProc(_az_output()))
    monkeypatch.setattr(azure_foundry.asyncio, "create_subprocess_exec", exec_mock)
    provider = azure_foundry.AzureCliTokenProvider("")
    assert await provider() == "tok"
    assert await provider() == "tok"
    assert exec_mock.await_count == 1
    args = exec_mock.await_args.args
    assert args[0] == "az"
    assert "--tenant" not in args
    assert azure_foundry.TOKEN_RESOURCE in args


async def test_cli_token_passes_configured_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    exec_mock = AsyncMock(return_value=_FakeProc(_az_output()))
    monkeypatch.setattr(azure_foundry.asyncio, "create_subprocess_exec", exec_mock)
    await azure_foundry.AzureCliTokenProvider(" contoso.onmicrosoft.com ")()
    args = exec_mock.await_args.args
    assert args[args.index("--tenant") + 1] == "contoso.onmicrosoft.com"


async def test_cli_token_refreshes_when_near_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    exec_mock = AsyncMock(return_value=_FakeProc(_az_output("old", time.time() + 60)))
    monkeypatch.setattr(azure_foundry.asyncio, "create_subprocess_exec", exec_mock)
    provider = azure_foundry.AzureCliTokenProvider()
    await provider()
    exec_mock.return_value = _FakeProc(_az_output("new"))
    assert await provider() == "new"


async def test_cli_token_errors_are_llm_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.services.llm.base import LLMError

    monkeypatch.setattr(
        azure_foundry.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError),
    )
    with pytest.raises(LLMError, match="Azure CLI not found"):
        await azure_foundry.AzureCliTokenProvider()()
    monkeypatch.setattr(
        azure_foundry.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_FakeProc(b"", b"ERROR: Please run 'az login'\n", 1)),
    )
    with pytest.raises(LLMError, match="az login"):
        await azure_foundry.AzureCliTokenProvider("t1")()


@pytest.mark.parametrize("tenant", ["--foo", "a b", "x;y"])
def test_invalid_tenant_is_rejected(tenant: str) -> None:
    with pytest.raises(ValueError):
        azure_foundry.AzureCliTokenProvider(tenant)


def test_client_auth_follows_api_key_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        azure_foundry, "AsyncAzureOpenAI", lambda **kw: seen.append(kw) or SimpleNamespace()
    )
    cfg = {"endpoint": "https://example.openai.azure.com", "tenant": "t1"}
    PROVIDERS["azure_foundry"].build({**cfg, "key": "secret"}, "")
    PROVIDERS["azure_foundry"].build(cfg, "")
    keyed, signed_in = seen[:2], seen[2:]
    assert all(kw["api_key"] == "secret" for kw in keyed)
    assert all("azure_ad_token_provider" not in kw for kw in keyed)
    assert all("api_key" not in kw for kw in signed_in)
    assert all(
        isinstance(kw["azure_ad_token_provider"], azure_foundry.AzureCliTokenProvider)
        for kw in signed_in
    )
    # Default chat version predates Responses, so that client gets a newer one.
    assert [kw["api_version"] for kw in keyed] == [
        azure_foundry.DEFAULT_API_VERSION,
        azure_foundry.RESPONSES_API_VERSION,
    ]


@pytest.mark.parametrize("suffix", ["/openai/v1", "/openai/v1/"])
def test_v1_endpoint_uses_plain_openai_client(suffix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        azure_foundry, "AsyncOpenAI", lambda **kw: seen.append(kw) or SimpleNamespace()
    )
    monkeypatch.setattr(azure_foundry, "AsyncAzureOpenAI", lambda **_: pytest.fail("azure client"))
    base = "https://example.openai.azure.com"
    PROVIDERS["azure_foundry"].build({"endpoint": base + suffix, "key": "secret"}, "")
    PROVIDERS["azure_foundry"].build({"endpoint": base + suffix}, "")
    assert seen[0] == {"base_url": base + "/openai/v1/", "api_key": "secret"}
    assert seen[1]["base_url"] == base + "/openai/v1/"
    assert isinstance(seen[1]["api_key"], azure_foundry.AzureCliTokenProvider)


def _v1_provider(monkeypatch: pytest.MonkeyPatch) -> azure_foundry.AzureFoundryProvider:
    monkeypatch.setattr(azure_foundry, "AsyncOpenAI", lambda **_: SimpleNamespace())
    return azure_foundry.AzureFoundryProvider(
        endpoint="https://example.openai.azure.com/openai/v1", api_key="k", deployment="claude"
    )


def _classic_provider(monkeypatch: pytest.MonkeyPatch) -> azure_foundry.AzureFoundryProvider:
    monkeypatch.setattr(azure_foundry, "AsyncAzureOpenAI", lambda **_: SimpleNamespace())
    return azure_foundry.AzureFoundryProvider(
        endpoint="https://example.openai.azure.com", api_key="k", deployment="claude"
    )


def _surfaces(monkeypatch: pytest.MonkeyPatch, *, working: str, calls: list[str]) -> None:
    from precursor.backend.services.llm.base import LLMError

    def make(name: str):  # type: ignore[no-untyped-def]
        async def run(**_: object) -> AsyncIterator[TextDeltaEvent]:
            calls.append(name)
            if name != working:
                raise LLMError("Requested API is currently not supported")
            yield TextDeltaEvent(content=name)

        return run

    monkeypatch.setattr(azure_foundry, "stream_responses_tools", make("responses"))
    monkeypatch.setattr(azure_foundry, "stream_openai_tools", make("chat"))


async def _turn(provider: azure_foundry.AzureFoundryProvider, model: str = "m") -> list[object]:
    return [e async for e in provider.stream_chat_with_tools(model=model, messages=[], tools=[])]


@pytest.mark.parametrize(
    ("style", "working", "expected_calls"),
    [
        ("v1", "responses", ["responses"]),
        ("v1", "chat", ["responses", "chat"]),
        ("classic", "chat", ["chat"]),
        ("classic", "responses", ["chat", "responses"]),
    ],
)
async def test_both_apis_are_tried_whatever_the_endpoint_style(
    style: str, working: str, expected_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    _surfaces(monkeypatch, working=working, calls=calls)
    provider = (_v1_provider if style == "v1" else _classic_provider)(monkeypatch)
    assert await _turn(provider) == [TextDeltaEvent(content=working)]
    assert calls == expected_calls
    calls.clear()
    # The working API is remembered, so the next turn goes straight to it.
    assert await _turn(provider) == [TextDeltaEvent(content=working)]
    assert calls == [working]


async def test_both_failing_raises_the_first_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.services.llm.base import LLMError

    async def fail(**_: object) -> AsyncIterator[TextDeltaEvent]:
        raise LLMError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(azure_foundry, "stream_responses_tools", fail)
    monkeypatch.setattr(azure_foundry, "stream_openai_tools", fail)
    with pytest.raises(LLMError, match="boom"):
        await _turn(_v1_provider(monkeypatch))


async def test_auth_failures_are_not_retried_on_the_other_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def responses(**_: object) -> AsyncIterator[TextDeltaEvent]:
        raise azure_foundry.AzureCliError("run az login")
        yield  # pragma: no cover

    chat = AsyncMock()
    monkeypatch.setattr(azure_foundry, "stream_responses_tools", responses)
    monkeypatch.setattr(azure_foundry, "stream_openai_tools", chat)
    provider = _v1_provider(monkeypatch)
    with pytest.raises(azure_foundry.AzureCliError):
        [e async for e in provider.stream_chat_with_tools(model="m", messages=[], tools=[])]
    chat.assert_not_called()


async def test_responses_call_uses_the_responses_capable_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[object] = []
    monkeypatch.setattr(
        azure_foundry, "AsyncAzureOpenAI", lambda **kw: clients.append(kw) or SimpleNamespace(**kw)
    )
    used: dict[str, object] = {}

    async def responses(**kw: object) -> AsyncIterator[TextDeltaEvent]:
        used["client"] = kw["client"]
        yield TextDeltaEvent(content="r")

    monkeypatch.setattr(azure_foundry, "stream_responses_tools", responses)
    monkeypatch.setattr(azure_foundry, "stream_openai_tools", _refuse)
    provider = azure_foundry.AzureFoundryProvider(
        endpoint="https://e.openai.azure.com", api_key="k"
    )
    assert await _turn(provider) == [TextDeltaEvent(content="r")]
    assert used["client"].api_version == azure_foundry.RESPONSES_API_VERSION  # type: ignore[attr-defined]


async def _refuse(**_: object) -> AsyncIterator[TextDeltaEvent]:
    from precursor.backend.services.llm.base import LLMError

    raise LLMError("not supported")
    yield  # pragma: no cover
