"""Permission-request classification helpers for agent sessions.

Pure functions extracted from the agent manager: they describe an SDK
permission request for the UI and decide auto-approval, with no session state.
"""

from __future__ import annotations

from typing import Any


def permission_signature(info: dict[str, Any]) -> tuple[str, str | None]:
    """A stable key for "approve for session": the action and its target."""
    target = (
        info.get("command")
        or info.get("path")
        or info.get("url")
        or info.get("tool")
        or info.get("server")
    )
    return (str(info.get("type", "tool")), str(target) if target else None)


def should_auto_approve(request: Any) -> bool:
    name = type(request).__name__
    if name in ("PermissionRequestRead", "PermissionRequestUrl"):
        return True
    if name == "PermissionRequestMcp":
        server = str(getattr(request, "server_name", "") or "")
        return server == "precursor" or bool(getattr(request, "read_only", False))
    return False


def describe_permission(request: Any) -> dict[str, Any]:
    """Normalise a permission request into a UI-friendly description."""

    def g(attr: str) -> Any:
        value = getattr(request, attr, None)
        return value if value not in ("",) else None

    name = type(request).__name__.replace("PermissionRequest", "") or "Tool"
    info: dict[str, Any] = {"type": name.lower(), "title": f"{name} permission"}
    if name == "Shell":
        info.update(
            title="Run a shell command",
            command=g("full_command_text"),
            intention=g("intention"),
            warning=g("warning"),
        )
    elif name == "Write":
        info.update(
            title="Write to a file",
            path=g("file_name"),
            intention=g("intention"),
            diff=(str(g("diff"))[:4000] if g("diff") else None),
        )
    elif name == "Read":
        info.update(title="Read a file", path=g("path"), intention=g("intention"))
    elif name == "Mcp":
        tool = g("tool_title") or g("tool_name")
        info.update(
            title=f"Use MCP tool: {tool}" if tool else "Use an MCP tool",
            server=g("server_name"),
            tool=g("tool_name"),
        )
    elif name == "Url":
        info.update(title="Fetch a URL", url=g("url"), intention=g("intention"))
    elif name == "Memory":
        info.update(title="Update memory", fact=g("fact"), reason=g("reason"))
    elif name == "CustomTool":
        tool = g("tool_name")
        info.update(
            title=f"Use tool: {tool}" if tool else "Use a tool",
            tool=tool,
            detail=g("tool_description"),
        )
    return {k: v for k, v in info.items() if v is not None}
