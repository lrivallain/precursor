"""Tests for the warm MCP session pool (MCPClientManager.acquire)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from precursor.backend.services.mcp.client import MCPClientManager, MCPToolDef


class _FakeSession:
    """Stand-in for an MCP ClientSession that records tool calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, args))
        return {"echo": name, "args": args}


def _patch_transport(manager: MCPClientManager) -> dict[str, int]:
    """Replace ``_open_transport`` with a fake; return an open-count tracker."""
    opens: dict[str, int] = {}
    sessions: dict[str, _FakeSession] = {}

    @asynccontextmanager
    async def fake_open(name: str, *, github_token: str = ""):
        opens[name] = opens.get(name, 0) + 1
        session = sessions.setdefault(name, _FakeSession())
        tools = [
            MCPToolDef(
                server=name,
                name="ping",
                description="",
                input_schema={"type": "object", "properties": {}},
            )
        ]
        yield session, tools

    manager._open_transport = fake_open  # type: ignore[assignment]
    manager._fake_sessions = sessions  # type: ignore[attr-defined]
    return opens


async def test_acquire_aggregates_and_routes_tool_calls() -> None:
    manager = MCPClientManager()
    _patch_transport(manager)

    active = await manager.acquire(["alpha", "beta"], github_token="t")
    try:
        assert {t.qualified_name for t in active.tools} == {"alpha__ping", "beta__ping"}
        assert active.tool_to_server["alpha__ping"] == ("alpha", "ping")
        assert not active.unavailable

        result = await active.call_tool("alpha", "ping", {"x": 1})
        assert result == {"echo": "ping", "args": {"x": 1}}
        assert manager._fake_sessions["alpha"].calls == [("ping", {"x": 1})]
    finally:
        await manager.aclose()


async def test_sessions_are_reused_across_turns() -> None:
    manager = MCPClientManager()
    opens = _patch_transport(manager)
    try:
        await manager.acquire(["alpha"])
        await manager.acquire(["alpha"])
        # Warm pool: the transport is opened once and reused, not per turn.
        assert opens["alpha"] == 1
    finally:
        await manager.aclose()


async def test_failed_server_is_reported_not_raised() -> None:
    manager = MCPClientManager()

    @asynccontextmanager
    async def boom(name: str, *, github_token: str = ""):
        raise RuntimeError("nope")
        yield  # pragma: no cover

    manager._open_transport = boom  # type: ignore[assignment]

    active = await manager.acquire(["alpha"])
    assert active.tools == []
    assert active.unavailable and active.unavailable[0][0] == "alpha"
    assert "nope" in active.unavailable[0][1]
    # A failed worker is dropped so a later turn retries cleanly.
    assert "alpha" not in manager._workers
    await manager.aclose()


async def test_pooling_disabled_opens_fresh_each_turn() -> None:
    manager = MCPClientManager()
    opens = _patch_transport(manager)
    import precursor.backend.services.mcp.client as client_mod

    original = client_mod.get_settings

    class _Settings:
        mcp_idle_ttl_seconds = 0

    client_mod.get_settings = lambda: _Settings()  # type: ignore[assignment]
    try:
        a = await manager.acquire(["alpha"])
        b = await manager.acquire(["alpha"])
        assert opens["alpha"] == 2
        assert "alpha" not in manager._workers
        await a.aclose()
        await b.aclose()
    finally:
        client_mod.get_settings = original  # type: ignore[assignment]
        await manager.aclose()


async def test_concurrent_acquire_shares_one_worker() -> None:
    manager = MCPClientManager()
    opens = _patch_transport(manager)
    try:
        await asyncio.gather(
            manager.acquire(["alpha"]),
            manager.acquire(["alpha"]),
        )
        assert opens["alpha"] == 1
    finally:
        await manager.aclose()
