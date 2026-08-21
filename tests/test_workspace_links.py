"""Tests for the workspace deep links MCP write results carry."""

from __future__ import annotations

from pathlib import Path

import pytest

from precursor.backend.services.mcp import workspace_links as links


def test_url_matches_the_spa_route() -> None:
    assert links.workspace_file_url("diagrams", "architecture/hub.drawio") == (
        "/ws/diagrams/architecture/hub.drawio"
    )


def test_url_encodes_each_segment_but_keeps_separators() -> None:
    url = links.workspace_file_url("my ws", "a b/c&d/e.drawio")

    assert url == "/ws/my%20ws/a%20b/c%26d/e.drawio"


def test_url_refuses_a_traversal_path() -> None:
    # A browser resolves ".." in a pushState URL, so a link built from such a
    # path would leave the /ws route entirely. safe_join blocks these before a
    # write succeeds; refusing here keeps a bad link from ever being advertised.
    assert links.workspace_file_url("ws", "../../etc/passwd") is None
    assert links.workspace_file_url("ws", "a/../../b.md") is None
    assert links.workspace_file_url("ws", "a/./b.md") is None


def test_url_refuses_an_empty_slug_or_path() -> None:
    assert links.workspace_file_url("", "a.md") is None
    assert links.workspace_file_url("ws", "") is None
    assert links.workspace_file_url("ws", "///") is None


def test_url_drops_empty_segments() -> None:
    assert links.workspace_file_url("ws", "/a//b.md") == "/ws/ws/a/b.md"


def test_with_open_link_preserves_the_original_result() -> None:
    out = links.with_open_link({"path": "a.drawio", "written": True}, "ws", "a.drawio")

    assert out["path"] == "a.drawio"
    assert out["written"] is True
    assert out["workspace_slug"] == "ws"
    assert out["url"] == "/ws/ws/a.drawio"


def test_with_open_link_omits_an_unsafe_link_but_keeps_the_result() -> None:
    out = links.with_open_link({"path": "../x", "written": True}, "ws", "../x")

    assert out == {"path": "../x", "written": True}


class _Result:
    """Stand-in for an MCP ``CallToolResult``."""

    def __init__(self, structured: object) -> None:
        self.structuredContent = structured


def test_link_from_result_reads_the_structured_payload() -> None:
    # The file body is deliberately large: the point of reading structured
    # content is that it is never parsed out of the serialised result text.
    result = _Result(
        {
            "path": "docs/a.drawio",
            "workspace_slug": "ws",
            "url": "/ws/ws/docs/a.drawio",
            "xml": "<mxfile />" * 100_000,
        }
    )

    assert links.link_from_result(result) == {"slug": "ws", "path": "docs/a.drawio"}


def test_link_from_result_ignores_unrelated_or_failed_results() -> None:
    assert links.link_from_result(_Result(None)) is None
    assert links.link_from_result(_Result({"answer": 42})) is None
    assert links.link_from_result(_Result("not a dict")) is None
    assert links.link_from_result(object()) is None
    assert (
        links.link_from_result(
            _Result({"error": "File not found", "path": "a.md", "workspace_slug": "ws"})
        )
        is None
    )


def test_link_from_result_rejects_a_hostile_server_payload() -> None:
    # Only slug + path are taken; a `url` the server made up is never trusted,
    # and a traversal path is refused outright.
    hostile = _Result(
        {
            "path": "../../etc/passwd",
            "workspace_slug": "ws",
            "url": "https://evil.example/steal",
        }
    )

    assert links.link_from_result(hostile) is None

    spoofed_url = _Result({"path": "a.md", "workspace_slug": "ws", "url": "javascript:alert(1)"})

    # The url is dropped; the caller rebuilds it from slug + path.
    assert links.link_from_result(spoofed_url) == {"slug": "ws", "path": "a.md"}


@pytest.mark.asyncio
async def test_create_diagram_result_links_to_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.services.mcp import drawio_server as srv

    class _WS:
        slug = "diagrams"
        subdir = None

    root = tmp_path / "diagrams"
    root.mkdir()
    monkeypatch.setattr(srv, "_load_workspace", _stub_workspace(_WS()))
    monkeypatch.setattr(srv, "_browse_root", lambda ws: root)

    result = await srv.create_diagram(
        workspace_id=1,
        path="architecture/hub",
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        edges=[{"source": "a", "target": "b"}],
    )

    assert result["written"] is True
    # The suffix the server appended must be reflected in the link.
    assert result["path"] == "architecture/hub.drawio"
    assert result["url"] == "/ws/diagrams/architecture/hub.drawio"
    assert result["workspace_slug"] == "diagrams"


@pytest.mark.asyncio
async def test_a_failed_write_carries_no_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.services.mcp import drawio_server as srv

    class _WS:
        slug = "diagrams"
        subdir = None

    root = tmp_path / "diagrams"
    root.mkdir()
    (root / "taken.drawio").write_text("<mxfile/>", encoding="utf-8")
    monkeypatch.setattr(srv, "_load_workspace", _stub_workspace(_WS()))
    monkeypatch.setattr(srv, "_browse_root", lambda ws: root)

    result = await srv.create_diagram(
        workspace_id=1, path="taken.drawio", nodes=[{"id": "a", "label": "A"}]
    )

    assert "error" in result
    assert "url" not in result


@pytest.mark.asyncio
async def test_workspace_fs_write_result_links_to_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.services.mcp import workspace_fs_server as srv

    class _WS:
        slug = "notes"
        subdir = None

    root = tmp_path / "notes"
    root.mkdir()
    monkeypatch.setattr(srv, "_load_workspace", _stub_workspace(_WS()))
    monkeypatch.setattr(srv, "_browse_root", lambda ws: root)

    written = await srv.write_file(workspace_id=1, path="docs/plan.md", content="# Plan")
    created = await srv.create_file(workspace_id=1, path="docs/new.md", content="x")
    folder = await srv.create_folder(workspace_id=1, path="docs/sub")

    assert written["url"] == "/ws/notes/docs/plan.md"
    assert created["url"] == "/ws/notes/docs/new.md"
    # A folder isn't a file the editor can open, so it carries no link.
    assert "url" not in folder


@pytest.mark.asyncio
async def test_read_tools_also_link_to_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.services.mcp import drawio_server as drawio
    from precursor.backend.services.mcp import workspace_fs_server as wsfs

    class _WS:
        slug = "notes"
        subdir = None

    root = tmp_path / "notes"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("# A", encoding="utf-8")
    (root / "docs" / "d.drawio").write_text("<mxfile />", encoding="utf-8")
    for srv in (drawio, wsfs):
        monkeypatch.setattr(srv, "_load_workspace", _stub_workspace(_WS()))
        monkeypatch.setattr(srv, "_browse_root", lambda ws: root)

    read = await wsfs.read_file(workspace_id=1, path="docs/a.md")
    diagram = await drawio.read_diagram(workspace_id=1, path="docs/d.drawio")

    assert read["content"] == "# A"
    assert read["url"] == "/ws/notes/docs/a.md"
    assert diagram["xml"] == "<mxfile />"
    assert diagram["url"] == "/ws/notes/docs/d.drawio"


@pytest.mark.asyncio
async def test_a_failed_read_carries_no_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.services.mcp import workspace_fs_server as wsfs

    class _WS:
        slug = "notes"
        subdir = None

    root = tmp_path / "notes"
    (root / "sub").mkdir(parents=True)
    monkeypatch.setattr(wsfs, "_load_workspace", _stub_workspace(_WS()))
    monkeypatch.setattr(wsfs, "_browse_root", lambda ws: root)

    missing = await wsfs.read_file(workspace_id=1, path="nope.md")
    directory = await wsfs.read_file(workspace_id=1, path="sub")

    assert "url" not in missing
    assert "url" not in directory


@pytest.mark.asyncio
async def test_link_survives_a_real_mcp_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link must come back through the actual MCP protocol, not just in-process.

    ``link_from_result`` reads ``structuredContent``, which FastMCP only
    populates for tools annotated to return a dict. Calling the real tool over a
    real client session is what proves the field is actually on the wire.
    """
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from precursor.backend.services.mcp import drawio_server as srv

    class _WS:
        slug = "diagrams"
        subdir = None

    root = tmp_path / "diagrams"
    root.mkdir()
    monkeypatch.setattr(srv, "_load_workspace", _stub_workspace(_WS()))
    monkeypatch.setattr(srv, "_browse_root", lambda ws: root)

    async with connect(srv.mcp._mcp_server) as client:
        written = await client.call_tool(
            "create_diagram",
            {
                "workspace_id": 1,
                "path": "architecture/hub",
                "nodes": [{"id": "a", "label": "A"}],
            },
        )
        read = await client.call_tool(
            "read_diagram",
            {"workspace_id": 1, "path": "architecture/hub.drawio"},
        )

    expected = {"slug": "diagrams", "path": "architecture/hub.drawio"}
    assert links.link_from_result(written) == expected
    assert links.link_from_result(read) == expected


def _stub_workspace(ws: object):
    async def _load(_workspace_id: int) -> object:
        return ws

    return _load
