"""Deep links to a workspace file, shared by the file MCP servers.

When a tool reads or writes a file in a workspace working tree, its result
carries a ``workspace_slug`` + ``url`` alongside the path. The SPA renders that
as an "Open" chip on the tool bubble, so a file the assistant just created or
inspected during a conversation is one click away in the Files section instead
of something you have to go hunting for.

Kept here rather than in a router so the stdio MCP subprocesses can build a link
without importing the LLM/git stack.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

# Mirrors the SPA's `/ws/<slug>/<file/path>` route (frontend/src/App.tsx).
_WS_ROUTE = "/ws"
# Segments a browser would resolve away, changing where the link points.
_UNSAFE_SEGMENTS = {".", ".."}


def workspace_file_url(slug: str, path: str) -> str | None:
    """Build the SPA deep link that opens ``path`` in workspace ``slug``.

    Returns ``None`` for a path that wouldn't survive URL resolution — a ``..``
    segment would climb out of the ``/ws`` route once the browser normalises it.
    ``safe_join`` already rejects such a path before a write succeeds, so this is
    belt-and-braces rather than the primary guard.
    """
    if not slug:
        return None
    segments = [seg for seg in path.split("/") if seg]
    if not segments or any(seg in _UNSAFE_SEGMENTS for seg in segments):
        return None
    encoded = "/".join(quote(seg, safe="") for seg in segments)
    return f"{_WS_ROUTE}/{quote(slug, safe='')}/{encoded}"


def with_open_link(result: dict[str, Any], slug: str, path: str) -> dict[str, Any]:
    """Annotate a successful file result with the deep link that opens it.

    The result is returned unchanged when no safe link can be built, so a tool
    never advertises a link that would land somewhere else.
    """
    url = workspace_file_url(slug, path)
    if url is None:
        return result
    return {**result, "workspace_slug": slug, "url": url}


def link_from_result(payload: Any) -> dict[str, str] | None:
    """Pull the workspace file a tool result points at, for the UI's link.

    Reads the MCP result's ``structuredContent`` — the dict the tool returned —
    so a read result's file contents are never parsed just to find a link.
    Returns only ``slug``/``path``: the caller rebuilds the URL itself rather
    than trusting a ``url`` string from what may be a third-party MCP server.
    """
    structured = getattr(payload, "structuredContent", None)
    if not isinstance(structured, dict) or structured.get("error"):
        return None
    slug = structured.get("workspace_slug")
    path = structured.get("path")
    if not isinstance(slug, str) or not isinstance(path, str):
        return None
    if workspace_file_url(slug, path) is None:
        return None
    return {"slug": slug, "path": path}
