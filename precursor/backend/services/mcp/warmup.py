"""Background warm-up for the enabled MCP servers.

Nothing used to connect at startup, so the *first* prompt of a session paid
connect + initialize + list_tools for every enabled server at once — and, for
the stdio ones, an ``npx`` spin-up on top. Because ``acquire`` waits for them
concurrently, the slowest server set the floor for time-to-first-token.

This ticker moves that cost off the critical path. It runs entirely in the
background:

* it never blocks startup or any request — the lifespan starts it and moves on;
* it warms **one server at a time**, since several built-ins launch ``npx`` and a
  startup thundering herd is precisely the stall being removed;
* it never initiates an interactive sign-in. A server whose credential is known
  to need one is skipped and left to the existing ``needs_auth`` machinery, so
  warm-up can never pop a browser window at you on launch.

Each server that resolves publishes ``mcp.server_state`` so an open Settings
panel updates live instead of waiting for its own probe.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from precursor.backend.config import Settings, get_settings

logger = logging.getLogger(__name__)


class MCPWarmUp:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._settings.mcp_warmup_enabled:
            logger.info("MCP warm-up disabled via settings.")
            return
        self._task = asyncio.create_task(self._run(), name="mcp-warmup")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        try:
            await asyncio.sleep(max(0.0, self._settings.mcp_warmup_delay_seconds))
            names = await self._targets()
            if not names:
                return
            logger.info("Warming %d MCP server(s) in the background", len(names))
            gap = max(0.0, self._settings.mcp_warmup_gap_seconds)
            for index, name in enumerate(names):
                if index:
                    await asyncio.sleep(gap)
                await self._warm(name)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - a failed warm-up must stay invisible
            logger.warning("MCP warm-up sweep failed", exc_info=True)

    async def _targets(self) -> list[str]:
        """Enabled servers worth connecting, in catalogue order."""
        from precursor.backend.db import SessionLocal
        from precursor.backend.services.mcp.client import get_mcp_client_manager
        from precursor.backend.services.turn_engine import load_enabled_mcp_servers

        async with SessionLocal() as session:
            enabled = set(await load_enabled_mcp_servers(session))
        manager = get_mcp_client_manager()
        return [entry.name for entry in manager.list_entries() if entry.name in enabled]

    def _skip_reason(self, name: str) -> str | None:
        """Why ``name`` should be left alone this sweep, or ``None`` to warm it."""
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        manager = get_mcp_client_manager()
        entry = manager.get(name)
        if entry is None:
            return "no such server"
        if entry.state in ("connected", "ready") and not entry.tools_from_cache:
            return "already connected"
        # Never trigger a browser sign-in from a background sweep: a stale
        # credential stays parked in needs_auth and the existing banner asks for
        # it when (and only when) the user actually needs the server.
        if entry.state == "needs_auth" or manager.is_auth_short_circuited(name):
            return "needs an interactive sign-in"
        return None

    async def _warm(self, name: str) -> None:
        from precursor.backend.db import SessionLocal
        from precursor.backend.services.events import publish_mcp_server_state
        from precursor.backend.services.github_auth import resolve_github_token
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        reason = self._skip_reason(name)
        if reason is not None:
            logger.debug("MCP warm-up skipped %s (%s)", name, reason)
            return
        manager = get_mcp_client_manager()
        async with SessionLocal() as session:
            github_token = await resolve_github_token(session)
        try:
            # ``acquired`` leaves the session warm in the pool; when pooling is
            # disabled it closes the one-shot session on exit, and the sweep is
            # still worth running because it refreshes the persisted catalogue.
            async with manager.acquired([name], github_token=github_token) as bundle:
                for server, err in bundle.unavailable:
                    logger.info("MCP warm-up could not reach %s: %s", server, err)
        except Exception:  # pragma: no cover - defensive: per-server isolation
            logger.debug("MCP warm-up failed for %s", name, exc_info=True)
        entry = manager.get(name)
        if entry is not None:
            with contextlib.suppress(Exception):
                await publish_mcp_server_state(name, entry.state, tools=len(entry.tools))


_warmup: MCPWarmUp | None = None


def get_mcp_warmup() -> MCPWarmUp:
    global _warmup
    if _warmup is None:
        _warmup = MCPWarmUp()
    return _warmup
