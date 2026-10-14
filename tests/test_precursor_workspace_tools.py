"""Workspace tools in the ``precursor`` MCP server (folded in from ``workspace-fs``)."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import AppSetting, Workspace
from precursor.backend.services.agents.mcp_scope import parse_mcp_scope
from precursor.backend.services.agents.permissions import should_auto_approve
from precursor.backend.services.app_settings import MCP_EXPOSE_SECTIONS
from precursor.backend.services.mcp import precursor_server as ps
from precursor.backend.services.mcp.client import ActiveTools, MCPClientManager, MCPToolDef
from precursor.backend.services.workspaces import WORKSPACE_READ_TOOLS, WORKSPACE_WRITE_TOOLS


async def _set_expose(value: dict[str, bool]) -> None:
    from precursor.backend.db import init_db

    await init_db()
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_expose")
        if row is None:
            session.add(AppSetting(key="mcp_expose", value=json.dumps(value)))
        else:
            row.value = json.dumps(value)
        await session.commit()


@pytest.fixture(autouse=True)
async def _reset_expose():
    yield
    await _set_expose({})


def test_workspace_sections_are_registered_and_tools_match() -> None:
    assert {"workspaces", "workspaces_write"} <= set(MCP_EXPOSE_SECTIONS)
    registered = {fn.__name__ for fn in ps._registrar.tools}
    assert registered >= (WORKSPACE_READ_TOOLS | WORKSPACE_WRITE_TOOLS)


async def test_workspace_tools_are_gated_by_default() -> None:
    await _set_expose({})
    for result in (
        await ps.list_workspaces(),
        await ps.create_workspace(name="x", kind="local"),
        await ps.write_workspace_file(workspace_id=1, path="a.md", content="x"),
    ):
        assert "not exposed" in result["error"]


async def test_workspace_sections_ignore_full_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """First-party agents run with full access; file writes still need opting in."""
    monkeypatch.setenv("PRECURSOR_MCP_FULL_ACCESS", "1")
    await _set_expose({})
    assert await ps._section_enabled("topics") is True
    assert await ps._section_enabled("workspaces", strict=True) is False
    assert "not exposed" in (await ps.create_workspace(name="x"))["error"]
    await _set_expose({"workspaces_write": True})
    assert await ps._section_enabled("workspaces_write", strict=True) is True


async def test_create_local_workspace_makes_a_ready_folder() -> None:
    await _set_expose({"workspaces": True, "workspaces_write": True})
    result = await ps.create_workspace(name="MCP Made Ws", kind="local")
    assert "error" not in result
    assert result["kind"] == "local"
    assert result["ready"] is True
    assert result["url"].endswith(f"/ws/{result['slug']}")
    assert (Path(get_settings().workspaces_dir) / result["slug"]).is_dir()

    # The new workspace is immediately usable through the file tools.
    written = await ps.write_workspace_file(
        workspace_id=result["workspace_id"], path="notes/a.md", content="# hi"
    )
    assert written["written"] is True
    read = await ps.read_workspace_file(workspace_id=result["workspace_id"], path="notes/a.md")
    assert read["content"] == "# hi"
    listing = await ps.list_workspaces()
    assert any(w["id"] == result["workspace_id"] for w in listing["workspaces"])


async def test_create_workspace_allocates_a_unique_slug() -> None:
    await _set_expose({"workspaces_write": True})
    first = await ps.create_workspace(name="Twin Space")
    second = await ps.create_workspace(name="Twin Space")
    assert first["slug"] != second["slug"]
    assert second["slug"].startswith(first["slug"])


async def test_create_workspace_never_takes_the_definitions_slug() -> None:
    from precursor.backend.config import DEFINITIONS_WORKSPACE_SLUG

    await _set_expose({"workspaces_write": True})
    result = await ps.create_workspace(name="x", slug=DEFINITIONS_WORKSPACE_SLUG)
    assert result["slug"] != DEFINITIONS_WORKSPACE_SLUG


async def test_create_git_workspace_requires_a_url_and_a_name() -> None:
    await _set_expose({"workspaces_write": True})
    assert "repository URL" in (await ps.create_workspace(name="g", kind="git"))["error"]
    assert "name" in (await ps.create_workspace(name="  "))["error"]
    async with SessionLocal() as session:
        names = (await session.execute(select(Workspace.name))).scalars().all()
    assert "g" not in names


async def test_workspace_writes_refuse_traversal() -> None:
    await _set_expose({"workspaces_write": True})
    made = await ps.create_workspace(name="Sandbox Ws")
    escaped = await ps.write_workspace_file(
        workspace_id=made["workspace_id"], path="../evil.txt", content="x"
    )
    assert "error" in escaped


# -- permissions -------------------------------------------------------------


def _mcp_request(server: str, tool: str, *, read_only: bool = False) -> Any:
    request_type = type("PermissionRequestMcp", (), {})
    request = request_type()
    request.server_name = server
    request.tool_name = tool
    request.read_only = read_only
    return request


def test_agents_still_prompt_for_workspace_writes() -> None:
    for tool in WORKSPACE_WRITE_TOOLS:
        assert should_auto_approve(_mcp_request("precursor", tool)) is False
    for tool in (*WORKSPACE_READ_TOOLS, "list_topics", "post_message"):
        assert should_auto_approve(_mcp_request("precursor", tool)) is True


# -- nested turns ------------------------------------------------------------


def _tool(name: str, server: str = "precursor") -> MCPToolDef:
    return MCPToolDef(
        server=server,
        name=name,
        description="",
        input_schema={"type": "object", "properties": {}},
    )


def test_restrict_server_tools_keeps_only_the_allowed_ones_and_survives_a_resync() -> None:
    bundle = ActiveTools(manager=MCPClientManager())
    tools = [_tool("post_message"), _tool("read_workspace_file"), _tool("list_topics")]
    tools.append(_tool("search", server="fetch"))
    bundle.tools[:] = tools
    for t in tools:
        bundle.tool_to_server[t.qualified_name] = (t.server, t.name)

    bundle.restrict_server_tools("precursor", WORKSPACE_READ_TOOLS | WORKSPACE_WRITE_TOOLS)

    assert {t.qualified_name for t in bundle.tools} == {
        "precursor__read_workspace_file",
        "fetch__search",
    }
    assert set(bundle.tool_to_server) == {"precursor__read_workspace_file", "fetch__search"}

    # A live refresh of the catalogue must not bring the dropped tools back.
    bundle._resync("precursor", tools[:3])
    assert "precursor__post_message" not in bundle.tool_to_server
    assert "precursor__read_workspace_file" in bundle.tool_to_server


# -- legacy scope ------------------------------------------------------------


def test_a_workspace_fs_scope_resolves_to_precursor() -> None:
    assert parse_mcp_scope("fetch, workspace-fs") == frozenset({"fetch", "precursor"})
    assert parse_mcp_scope("workspace-fs,precursor") == frozenset({"precursor"})


# -- data migration ----------------------------------------------------------


def _run_fold(settings: dict[str, object]) -> dict[str, Any]:
    versions = Path(__file__).resolve().parents[1] / "precursor/backend/alembic/versions"
    path = next(versions.glob("a7d2c91e4b35_*.py"))
    spec = importlib.util.spec_from_file_location("_fold_ws_fs", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)"))
            for key, value in settings.items():
                conn.execute(
                    sa.text("INSERT INTO app_settings VALUES (:k, :v)"),
                    {"k": key, "v": json.dumps(value)},
                )
        from unittest import mock

        with engine.begin() as conn, mock.patch.object(migration.op, "get_bind", return_value=conn):
            migration.upgrade()
        with engine.connect() as conn:
            return {
                k: json.loads(v)
                for k, v in conn.execute(sa.text("SELECT key, value FROM app_settings"))
            }
    finally:
        engine.dispose()
        os.unlink(db_path)


def test_migration_carries_an_enabled_workspace_fs_over() -> None:
    out = _run_fold(
        {"mcp_enabled": {"workspace-fs": True, "fetch": True}, "mcp_expose": {"topics": True}}
    )
    assert out["mcp_enabled"] == {"fetch": True, "precursor": True}
    assert out["mcp_expose"] == {"topics": True, "workspaces": True, "workspaces_write": True}


def test_migration_drops_a_disabled_workspace_fs_without_enabling_anything() -> None:
    out = _run_fold({"mcp_enabled": {"workspace-fs": False}})
    assert out["mcp_enabled"] == {}
    assert "mcp_expose" not in out


def test_migration_is_a_no_op_without_the_old_key() -> None:
    assert _run_fold({"mcp_enabled": {"fetch": True}}) == {"mcp_enabled": {"fetch": True}}
    assert _run_fold({}) == {}
