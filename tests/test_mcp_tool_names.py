"""Provider-safe MCP names must still route to the original server and tool."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from precursor.backend.plugins import LoadedPlugin, PluginRegistry
from precursor.backend.services.llm._openai_compat import to_openai_tools
from precursor.backend.services.llm._responses_compat import to_responses_tools
from precursor.backend.services.mcp.client import MCPClientManager, MCPToolDef
from precursor.backend.services.turn_engine import mcp_tools_to_provider


def _tool(server: str, name: str) -> MCPToolDef:
    return MCPToolDef(
        server=server,
        name=name,
        description="Read a board",
        input_schema={"type": "object", "properties": {}},
    )


@pytest.mark.parametrize(
    ("server", "name"),
    [
        ("github", "list_issues"),
        ("workspace-fs", "read_file"),
        ("alpha", "nested__tool"),
        ("alpha", "a" * 57),
    ],
)
def test_safe_unambiguous_names_are_unchanged(server: str, name: str) -> None:
    assert _tool(server, name).qualified_name == f"{server}__{name}"


@pytest.mark.parametrize(
    ("server", "name"),
    [
        ("kanban.board", "list_boards"),
        ("My server", "tools/read"),
        ("alpha", "board.summary"),
        ("alpha", "ping\n"),
        ("\u00e9quipe", "r\u00e9sum\u00e9"),
        ("alpha", "a" * 58),
        ("a" * 130, "b" * 130),
    ],
)
def test_invalid_or_long_names_get_stable_provider_safe_aliases(server: str, name: str) -> None:
    tool = _tool(server, name)
    alias = tool.qualified_name
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", alias)
    assert alias != f"{server}__{name}"
    assert alias == _tool(server, name).qualified_name
    assert tool.server == server
    assert tool.name == name
    tool.description = "A changed description must not change routing"
    assert tool.qualified_name == alias


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("kanban.board", "list_boards"), ("kanban_board", "list_boards")),
        (("kanban.board", "list_boards"), ("kanban/board", "list_boards")),
        (("alpha", "board.summary"), ("alpha", "board_summary")),
        (("alpha", "board.summary"), ("alpha", "board/summary")),
        (("alpha__beta", "ping"), ("alpha", "beta__ping")),
        (("alpha_", "ping"), ("alpha", "_ping")),
        (("alpha", "x" * 130 + "a"), ("alpha", "x" * 130 + "b")),
        (("x" * 130 + "a", "ping"), ("x" * 130 + "b", "ping")),
    ],
)
def test_aliases_disambiguate_normalization_truncation_and_namespace_boundaries(
    first: tuple[str, str], second: tuple[str, str]
) -> None:
    assert _tool(*first).qualified_name != _tool(*second).qualified_name


def test_a_raw_name_cannot_impersonate_an_alias() -> None:
    alias = _tool("kanban.board", "list_boards").qualified_name
    server, name = alias.split("__", 1)
    assert _tool(server, name).qualified_name != alias


@pytest.mark.parametrize("cached", [False, True])
async def test_plugin_aliases_reach_both_provider_apis_and_route_back_after_refresh(
    monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    registry = PluginRegistry()
    registry.plugins["kanban"] = LoadedPlugin(id="kanban")
    registry._current = "kanban"
    registry.add_mcp_server(name="board", module="kanban.mcp_server")
    registry._current = None
    (spec,) = registry.mcp_servers
    assert spec.name == "kanban.board"

    manager = MCPClientManager()
    entry = manager.register_plugin_entry(
        plugin_id="kanban",
        name=spec.name,
        transport=spec.transport,
        command=spec.command,
        args=spec.args,
    )
    tools = [_tool(spec.name, name) for name in ("list_boards", "board.summary")]
    entry.tools = tools
    session = AsyncMock()
    session.call_tool.return_value = {"boards": []}
    offline = cached

    @asynccontextmanager
    async def fake_open(name: str, *, github_token: str = ""):
        assert name == spec.name
        if offline:
            raise RuntimeError("offline")
        yield session, tools

    monkeypatch.setattr(manager, "_open_transport", fake_open)

    try:
        active = await manager.acquire([spec.name], advertise_cached=True)
        assert (spec.name in active.advertised_from_cache) is cached
        provider_tools = mcp_tools_to_provider(active.tools)
        aliases = [tool.name for tool in provider_tools]
        assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", alias) for alias in aliases)
        assert len(set(aliases)) == len(tools)
        assert [t["function"]["name"] for t in to_openai_tools(provider_tools)] == aliases
        assert [t["name"] for t in to_responses_tools(provider_tools)] == aliases

        offline = False
        for tool, provider_tool in zip(tools, provider_tools, strict=True):
            assert provider_tool.description == tool.description
            assert provider_tool.parameters == tool.input_schema
            server, raw_name = active.tool_to_server[provider_tool.name]
            assert (server, raw_name) == (spec.name, tool.name)
            assert await active.call_tool(server, raw_name, {}) == {"boards": []}
            session.call_tool.assert_awaited_with(tool.name, {})

        routes = active.tool_to_server
        replacement = _tool(spec.name, "get.board")
        active._resync(spec.name, [replacement, tools[0]])
        assert active.tool_to_server is routes
        assert aliases[1] not in routes
        assert routes[aliases[0]] == (spec.name, "list_boards")
        refreshed = mcp_tools_to_provider(active.tools)
        server, raw_name = routes[refreshed[0].name]
        assert (server, raw_name) == (spec.name, "get.board")
        await active.call_tool(server, raw_name, {"project_id": "demo"})
        session.call_tool.assert_awaited_with("get.board", {"project_id": "demo"})
    finally:
        await manager.aclose()
