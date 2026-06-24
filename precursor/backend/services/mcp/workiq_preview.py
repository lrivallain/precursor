"""WorkIQ *preview* mode — OAuth-backed streamable-HTTP transport for writes.

The built-in ``workiq`` MCP server normally runs the read-only local stdio
launcher (``npx @microsoft/workiq@latest mcp``). Preview mode points it at the
hosted endpoint ``https://workiq.svc.cloud.microsoft/mcp`` instead, which serves
the full read **and write** surface (``create_entity``/``update_entity``/
``delete_entity``/``do_action``/…).

That endpoint is OAuth-protected. We drive the MCP SDK's
:class:`~mcp.client.auth.OAuthClientProvider` with the WorkIQ-published public
client id and a loopback redirect on port 12798 (matching the Copilot CLI
plugin's ``redirectPort``). Tokens are persisted in ``AppSetting`` so the
interactive browser login only happens once per machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import webbrowser
from urllib.parse import parse_qs, urlsplit

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from precursor.backend.db import SessionLocal
from precursor.backend.models import AppSetting

logger = logging.getLogger(__name__)

# Hosted WorkIQ MCP endpoint (full read+write surface).
WORKIQ_PREVIEW_URL = "https://workiq.svc.cloud.microsoft/mcp"
# WorkIQ-published public OAuth client (same id the Copilot CLI plugin uses).
WORKIQ_OAUTH_CLIENT_ID = "ba081686-5d24-4bc6-a0d6-d034ecffed87"
# Loopback redirect; the port matches the plugin's ``auth.redirectPort``.
WORKIQ_OAUTH_REDIRECT_PORT = 12798
WORKIQ_OAUTH_REDIRECT_PATH = "/callback"
WORKIQ_OAUTH_REDIRECT_URI = (
    f"http://localhost:{WORKIQ_OAUTH_REDIRECT_PORT}{WORKIQ_OAUTH_REDIRECT_PATH}"
)

# AppSetting keys.
PREVIEW_FLAG_KEY = "mcp_workiq_preview"
OAUTH_TOKENS_KEY = "workiq_oauth_tokens"

_CALLBACK_TIMEOUT_SECONDS = 300.0


async def resolve_workiq_preview() -> bool:
    """Whether WorkIQ preview (hosted HTTP + writes) is enabled."""
    async with SessionLocal() as session:
        row = await session.get(AppSetting, PREVIEW_FLAG_KEY)
        return bool(row and row.value == "true")


async def set_workiq_preview(enabled: bool) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, PREVIEW_FLAG_KEY)
        encoded = "true" if enabled else "false"
        if row is None:
            session.add(AppSetting(key=PREVIEW_FLAG_KEY, value=encoded))
        else:
            row.value = encoded
        await session.commit()


class DbTokenStorage(TokenStorage):
    """OAuth token + client-info storage backed by the ``AppSetting`` table.

    ``client_info`` is fixed: we always hand the SDK the WorkIQ-published public
    client id so it skips dynamic registration (see ``OAuthClientProvider`` step
    4). Only the issued tokens are persisted, so a successful login survives app
    restarts.
    """

    _client_info = OAuthClientInformationFull(
        client_id=WORKIQ_OAUTH_CLIENT_ID,
        redirect_uris=[WORKIQ_OAUTH_REDIRECT_URI],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="Precursor (WorkIQ preview)",
    )

    async def get_tokens(self) -> OAuthToken | None:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, OAUTH_TOKENS_KEY)
        if row is None or not row.value or row.value == "null":
            return None
        try:
            return OAuthToken.model_validate_json(row.value)
        except ValueError:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        encoded = tokens.model_dump_json()
        async with SessionLocal() as session:
            row = await session.get(AppSetting, OAUTH_TOKENS_KEY)
            if row is None:
                session.add(AppSetting(key=OAUTH_TOKENS_KEY, value=encoded))
            else:
                row.value = encoded
            await session.commit()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        # The client is pre-registered; nothing to persist.
        return None


async def clear_workiq_oauth_tokens() -> None:
    """Forget any stored tokens so the next connect re-runs the browser login."""
    async with SessionLocal() as session:
        row = await session.get(AppSetting, OAUTH_TOKENS_KEY)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def _redirect_handler(authorization_url: str) -> None:
    """Open the user's browser at the WorkIQ authorization URL."""
    logger.info("WorkIQ preview: opening browser for sign-in: %s", authorization_url)
    try:
        webbrowser.open(authorization_url)
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.warning("WorkIQ preview: could not open a browser automatically: %s", exc)


async def _callback_handler() -> tuple[str, str | None]:
    """Run a one-shot loopback server and return ``(auth_code, state)``.

    Listens on ``127.0.0.1:WORKIQ_OAUTH_REDIRECT_PORT`` for the single OAuth
    redirect, parses ``code``/``state`` off the query string, replies with a
    minimal success page, and resolves.
    """
    loop = asyncio.get_running_loop()
    result: asyncio.Future[tuple[str, str | None]] = loop.create_future()

    async def _on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            target = ""
            parts = request_line.decode("latin-1").split(" ")
            if len(parts) >= 2:
                target = parts[1]
            query = parse_qs(urlsplit(target).query)
            code = query.get("code", [""])[0]
            state = query.get("state", [None])[0]
            error = query.get("error", [None])[0]

            if error:
                body = f"WorkIQ sign-in failed: {error}. You can close this tab."
            elif code:
                body = "WorkIQ sign-in complete. You can close this tab and return to Precursor."
            else:
                body = "No authorization code received. You can close this tab."

            payload = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body.encode('utf-8'))}\r\n"
                "Connection: close\r\n\r\n"
                f"{body}"
            )
            writer.write(payload.encode("utf-8"))
            await writer.drain()

            if not result.done():
                if code:
                    result.set_result((code, state))
                else:
                    result.set_exception(
                        RuntimeError(error or "No authorization code in OAuth callback")
                    )
        except Exception as exc:  # pragma: no cover - defensive
            if not result.done():
                result.set_exception(exc)
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    server = await asyncio.start_server(
        _on_connect, host="127.0.0.1", port=WORKIQ_OAUTH_REDIRECT_PORT
    )
    try:
        async with server:
            return await asyncio.wait_for(result, timeout=_CALLBACK_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise RuntimeError("Timed out waiting for the WorkIQ sign-in to complete.") from exc


def build_oauth_provider() -> OAuthClientProvider:
    """Build the OAuth provider used as the ``httpx.Auth`` for the HTTP transport."""
    client_metadata = OAuthClientMetadata(
        redirect_uris=[WORKIQ_OAUTH_REDIRECT_URI],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="Precursor (WorkIQ preview)",
    )
    return OAuthClientProvider(
        server_url=WORKIQ_PREVIEW_URL,
        client_metadata=client_metadata,
        storage=DbTokenStorage(),
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
    )
