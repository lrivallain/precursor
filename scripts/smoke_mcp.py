"""Exercise MCP on an installed wheel, against whatever SDK the resolver picked.

The release smoke gate (``smoke_release.py``) proves the app imports and serves;
this goes one step further for the MCP surfaces, which the app never imports on
its own: the in-tree stdio servers only load inside their subprocess, and the
streamable-HTTP client only runs when a server is reached. Both are driven here
through Precursor's own client, from outside the source checkout, with no access
to the developer's data.

Installed plugins' MCP servers run in the same environment and so must agree on
the SDK major; each one is started and listed too. Pass plugin ids as arguments
(``smoke_mcp.py kanban``) to fail when one of them contributes no MCP server —
otherwise a plugin that silently failed to load would pass vacuously.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import socket
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any


def _isolate(root: Path) -> None:
    os.chdir(root)
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        os.environ.pop(key, None)
    os.environ.update(
        HOME=str(root),
        GH_CONFIG_DIR=str(root / "gh"),
        PATH=str(Path(sys.executable).parent),
        XDG_CONFIG_HOME=str(root / "config"),
        COPILOT_HOME=str(root / "copilot"),
        PRECURSOR_DATABASE_URL=f"sqlite+aiosqlite:///{root / 'smoke.db'}",
        PRECURSOR_DATA_DIR=str(root / "data"),
        PRECURSOR_SKILLS_DIR=str(root / "skills"),
        PRECURSOR_MCP_WARMUP_ENABLED="false",
        PRECURSOR_WORKIQ_KEEPALIVE_ENABLED="false",
        PRECURSOR_PORT=str(_free_port()),
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _check_info(result: Any, expected_version: str) -> None:
    if result.is_error:
        raise RuntimeError(f"precursor_info returned an error result: {result.content}")
    info = result.structured_content
    if not isinstance(info, dict) or info.get("version") != expected_version:
        raise RuntimeError(f"precursor_info returned an unexpected payload: {info!r}")


async def _exercise(manager: Any, name: str, expected_version: str) -> int:
    async with manager.open_session(name) as (session, tools):
        if "precursor_info" not in {t.name for t in tools}:
            raise RuntimeError(f"{name}: precursor_info missing from {[t.name for t in tools]}")
        _check_info(await session.call_tool("precursor_info", {}), expected_version)
    # The pooled path, including a reconnect on a fresh session.
    bundle = await manager.acquire([name])
    if bundle.unavailable:
        raise RuntimeError(f"{name}: pool could not connect: {bundle.unavailable}")
    worker = bundle.workers[name]
    await manager.recycle_worker(name, worker)
    bundle.workers.pop(name)
    _check_info(await bundle.call_tool(name, "precursor_info", {}), expected_version)
    await manager.aclose()
    return len(tools)


async def _main(expected_plugins: set[str]) -> None:
    import httpx
    import uvicorn

    import precursor
    from precursor.backend.config import get_settings
    from precursor.backend.db import init_db
    from precursor.backend.main import create_app
    from precursor.backend.services.mcp.client import (
        BUILTIN_CATALOG,
        MCPClientManager,
        get_mcp_client_manager,
    )

    modules = [
        spec.args[1]
        for spec in BUILTIN_CATALOG
        if spec.transport == "stdio" and spec.args[:1] == ("-m",)
    ]
    for module in modules:
        importlib.import_module(module)

    await init_db()
    stdio_tools = await _exercise(MCPClientManager(), "precursor", precursor.__version__)

    port = get_settings().port
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if serving.done():
                serving.result()
            await asyncio.sleep(0.05)
        origin = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=origin) as client:
            (await client.put("/api/settings", json={"mcp_http_enabled": True})).raise_for_status()
        manager = MCPClientManager()
        manager.register_user_entry(name="self", transport="streamable_http", url=f"{origin}/mcp")
        http_tools = await _exercise(manager, "self", precursor.__version__)

        # The lifespan hydrated the plugins' servers into the shared manager.
        shared = get_mcp_client_manager()
        plugin_entries = [e for e in shared.list_entries() if e.plugin_id is not None]
        missing = expected_plugins - {e.plugin_id for e in plugin_entries}
        if missing:
            raise RuntimeError(f"expected plugins contributed no MCP server: {sorted(missing)}")
        plugin_tools: dict[str, int] = {}
        for entry in plugin_entries:
            async with shared.open_session(entry.name) as (_session, tools):
                if not tools:
                    raise RuntimeError(f"plugin MCP server {entry.name} exposes no tools")
                plugin_tools[entry.name] = len(tools)
        await shared.aclose()
    finally:
        server.should_exit = True
        await serving

    print(
        f"MCP {version('mcp')}: {len(modules)} stdio servers import; the built-in stdio server "
        f"({stdio_tools} tools) and the streamable-HTTP endpoint ({http_tools} tools) list, "
        f"call and reconnect; plugin MCP servers start and list: {plugin_tools or 'none'}."
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="precursor-mcp-smoke-") as directory:
        _isolate(Path(directory))
        from precursor.backend.logging_config import configure_logging

        configure_logging("warning")
        asyncio.run(_main(set(sys.argv[1:])))


if __name__ == "__main__":
    main()
