from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from precursor.backend.services import azure_cli


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
    monkeypatch.setattr(azure_cli.asyncio, "create_subprocess_exec", exec_mock)
    provider = azure_cli.AzureCliTokenProvider("")
    assert await provider() == "tok"
    assert await provider() == "tok"
    assert exec_mock.await_count == 1
    args = exec_mock.await_args.args
    assert args[0] == "az"
    assert "--tenant" not in args
    assert azure_cli.COGNITIVE_SERVICES_RESOURCE in args


async def test_cli_token_passes_configured_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    exec_mock = AsyncMock(return_value=_FakeProc(_az_output()))
    monkeypatch.setattr(azure_cli.asyncio, "create_subprocess_exec", exec_mock)
    await azure_cli.AzureCliTokenProvider(" contoso.onmicrosoft.com ")()
    args = exec_mock.await_args.args
    assert args[args.index("--tenant") + 1] == "contoso.onmicrosoft.com"


async def test_cli_token_refreshes_when_near_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    exec_mock = AsyncMock(return_value=_FakeProc(_az_output("old", time.time() + 60)))
    monkeypatch.setattr(azure_cli.asyncio, "create_subprocess_exec", exec_mock)
    provider = azure_cli.AzureCliTokenProvider()
    await provider()
    exec_mock.return_value = _FakeProc(_az_output("new"))
    assert await provider() == "new"


async def test_cli_token_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        azure_cli.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_FakeProc(b"", b"ERROR: Please run 'az login'\n", 1)),
    )
    with pytest.raises(azure_cli.AzureCliError, match="for tenant t1; run `az login`"):
        await azure_cli.AzureCliTokenProvider("t1")()


async def test_cli_token_requests_the_given_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    exec_mock = AsyncMock(return_value=_FakeProc(_az_output()))
    monkeypatch.setattr(azure_cli.asyncio, "create_subprocess_exec", exec_mock)
    await azure_cli.AzureCliTokenProvider(resource=azure_cli.ARM_RESOURCE)()
    args = exec_mock.await_args.args
    assert args[args.index("--resource") + 1] == azure_cli.ARM_RESOURCE


@pytest.mark.parametrize("tenant", ["--foo", "a b", "x;y"])
def test_invalid_tenant_is_rejected(tenant: str) -> None:
    with pytest.raises(ValueError):
        azure_cli.AzureCliTokenProvider(tenant)
