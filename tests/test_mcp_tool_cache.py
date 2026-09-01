"""Tests for the persisted MCP tool catalogue (``mcp_tool_cache``).

The catalogue used to live only in ``MCPServerEntry.tools``, so every restart
threw it away and both the Settings panel and the first chat prompt had to
reconnect each enabled server to learn what it exposes. These cover the cache
that removes that: the round-trip, the startup hydration, the provenance flag
that keeps a cached catalogue from being mistaken for a live session, and the
refresh-and-retry that stops a stale entry from turning into a phantom tool.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from precursor.backend.db import init_db
from precursor.backend.services.mcp import tool_cache
from precursor.backend.services.mcp.client import (
    MCPClientManager,
    MCPServerEntry,
    MCPToolDef,
)


def _tool(server: str, name: str, description: str = "") -> MCPToolDef:
    return MCPToolDef(
        server=server,
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )


class _SdkTool:
    """Shape of the SDK's ``list_tools()`` entries."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} tool"
        self.inputSchema = {"type": "object", "properties": {}}  # SDK spelling


class _SdkToolList:
    def __init__(self, names: list[str]) -> None:
        self.tools = [_SdkTool(n) for n in names]


class _FakeSession:
    """MCP session whose tool inventory can change under the cache's feet."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> _SdkToolList:
        return _SdkToolList(self.names)

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name not in self.names:
            raise RuntimeError(f"Unknown tool: {name}")
        return {"ok": name}


def _patch_transport(manager: MCPClientManager, session: _FakeSession) -> None:
    @asynccontextmanager
    async def fake_open(name: str, *, github_token: str = ""):  # type: ignore[no-untyped-def]
        entry = manager.get(name)
        tools = await manager._fetch_tools(name, session)
        if entry is not None:
            await manager._adopt_tools(entry, tools)
        yield session, tools

    manager._open_transport = fake_open  # type: ignore[assignment]


async def test_cache_round_trips() -> None:
    await init_db()
    await tool_cache.remember("alpha", [_tool("alpha", "ping", "check")])

    loaded = await tool_cache.load()
    assert [t.name for t in loaded["alpha"]] == ["ping"]
    assert loaded["alpha"][0].description == "check"
    assert loaded["alpha"][0].input_schema["properties"] == {"q": {"type": "string"}}

    await tool_cache.forget("alpha")
    assert "alpha" not in await tool_cache.load()


async def test_hydrate_seeds_entry_before_any_connect() -> None:
    """The whole point: a catalogue is available with zero connects."""
    await init_db()
    manager = MCPClientManager()
    entry = MCPServerEntry(name="precursor", transport="stdio", command="x")
    manager._servers = {"precursor": entry}
    await tool_cache.remember("precursor", [_tool("precursor", "list_topics")])

    from precursor.backend.services.mcp import client as client_module

    prev = client_module.get_mcp_client_manager
    client_module.get_mcp_client_manager = lambda: manager  # type: ignore[assignment]
    try:
        hydrated = await tool_cache.hydrate_entries()
    finally:
        client_module.get_mcp_client_manager = prev  # type: ignore[assignment]

    assert hydrated == 1
    assert [t.name for t in entry.tools] == ["list_topics"]
    # Provenance: the tools are known, the server is emphatically not connected.
    assert entry.tools_from_cache is True
    assert entry.state == "disconnected"
    await tool_cache.forget("precursor")


async def test_hydrate_never_overwrites_a_live_catalogue() -> None:
    await init_db()
    manager = MCPClientManager()
    entry = MCPServerEntry(name="precursor", transport="stdio", command="x")
    entry.tools = [_tool("precursor", "live_tool")]
    manager._servers = {"precursor": entry}
    await tool_cache.remember("precursor", [_tool("precursor", "cached_tool")])

    from precursor.backend.services.mcp import client as client_module

    prev = client_module.get_mcp_client_manager
    client_module.get_mcp_client_manager = lambda: manager  # type: ignore[assignment]
    try:
        assert await tool_cache.hydrate_entries() == 0
    finally:
        client_module.get_mcp_client_manager = prev  # type: ignore[assignment]

    assert [t.name for t in entry.tools] == ["live_tool"]
    assert entry.tools_from_cache is False
    await tool_cache.forget("precursor")


async def test_adopt_tools_persists_and_clears_cache_provenance() -> None:
    """A successful connect refreshes the stored catalogue."""
    await init_db()
    manager = MCPClientManager()
    entry = MCPServerEntry(name="alpha", transport="stdio", command="x")
    entry.tools_from_cache = True
    manager._servers = {"alpha": entry}

    await manager._adopt_tools(entry, [_tool("alpha", "fresh")])

    assert entry.tools_from_cache is False
    assert [t.name for t in (await tool_cache.load())["alpha"]] == ["fresh"]
    await tool_cache.forget("alpha")


async def test_status_dict_reports_cache_provenance() -> None:
    manager = MCPClientManager()
    entry = MCPServerEntry(name="alpha", transport="stdio", command="x")
    entry.tools = [_tool("alpha", "ping")]
    entry.tools_from_cache = True

    assert manager.status_dict(entry, enabled=True)["tools_from_cache"] is True
    entry.tools_from_cache = False
    assert manager.status_dict(entry, enabled=True)["tools_from_cache"] is False


async def test_stale_cached_tool_refreshes_and_retries() -> None:
    """A tool that vanished server-side re-lists the catalogue instead of failing blind."""
    await init_db()
    manager = MCPClientManager()
    entry = MCPServerEntry(name="alpha", transport="stdio", command="x")
    manager._servers = {"alpha": entry}
    # The live server exposes ``search``; the cache still remembers ``lookup``.
    session = _FakeSession(["search"])
    _patch_transport(manager, session)
    await tool_cache.remember("alpha", [_tool("alpha", "lookup")])

    try:
        active = await manager.acquire(["alpha"])
        raised = False
        try:
            await active.call_tool("alpha", "lookup", {})
        except RuntimeError:
            raised = True
        assert raised  # the tool really is gone; we don't invent a result

        # …but the stale entry was re-listed and the cache corrected, so the
        # next turn advertises what the server actually has.
        cached = (await tool_cache.load())["alpha"]
        assert [t.name for t in cached] == ["search"]
        assert [t.name for t in entry.tools] == ["search"]
        # Both attempts were made: the original call and the post-refresh retry.
        assert session.calls == [("lookup", {}), ("lookup", {})]
    finally:
        await manager.aclose()
        await tool_cache.forget("alpha")


async def test_refresh_and_retry_succeeds_when_the_tool_is_back() -> None:
    """The retry is a real second chance, not just a cache-repair side effect."""
    await init_db()
    manager = MCPClientManager()
    entry = MCPServerEntry(name="alpha", transport="stdio", command="x")
    manager._servers = {"alpha": entry}
    session = _FakeSession(["search"])
    _patch_transport(manager, session)

    try:
        active = await manager.acquire(["alpha"])

        original_call = session.call_tool
        state = {"first": True}

        async def flaky(name: str, args: dict[str, Any]) -> Any:
            if state["first"]:
                state["first"] = False
                session.calls.append((name, args))
                raise RuntimeError("Unknown tool: search")
            return await original_call(name, args)

        session.call_tool = flaky  # type: ignore[assignment]
        assert await active.call_tool("alpha", "search", {}) == {"ok": "search"}
    finally:
        await manager.aclose()
        await tool_cache.forget("alpha")


async def test_unrelated_failure_is_not_retried() -> None:
    """Only a missing-tool error triggers the refresh; real errors surface once."""
    await init_db()
    manager = MCPClientManager()
    entry = MCPServerEntry(name="alpha", transport="stdio", command="x")
    manager._servers = {"alpha": entry}
    session = _FakeSession(["search"])
    _patch_transport(manager, session)

    async def boom(name: str, args: dict[str, Any]) -> Any:
        session.calls.append((name, args))
        raise RuntimeError("upstream returned 500")

    session.call_tool = boom  # type: ignore[assignment]
    try:
        active = await manager.acquire(["alpha"])
        raised = False
        try:
            await active.call_tool("alpha", "search", {})
        except RuntimeError as exc:
            raised = "500" in str(exc)
        assert raised
        assert len(session.calls) == 1
    finally:
        await manager.aclose()
        await tool_cache.forget("alpha")
