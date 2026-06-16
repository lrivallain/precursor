"""FastAPI application entrypoint.

Serves both the JSON API (under ``/api``) and the built React SPA (mounted at
``/``). In development, run Vite separately on :5173 and let it proxy to this
process; in production a single uvicorn handles everything.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from starlette.routing import Route

from precursor import __version__
from precursor.backend.config import get_settings
from precursor.backend.db import init_db
from precursor.backend.plugins import discover, get_registry
from precursor.backend.routers import (
    attachments,
    chat,
    commands,
    events,
    github,
    issue,
    llm,
    mcp,
    me,
    memories,
    raw,
    schedules,
    settings,
    skills,
    stt,
    summary,
    topics,
    version,
    workspaces,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    from precursor.backend.services.mcp.user_servers import hydrate_user_entries

    await hydrate_user_entries()
    discover(app)
    from precursor.backend.services.scheduler import get_scheduler

    scheduler = get_scheduler()
    await scheduler.start()
    try:
        # The mounted streamable-HTTP MCP app needs its session manager's task group
        # running for the lifetime of the server (the mount itself doesn't start it).
        mcp_instance = getattr(app.state, "precursor_http_mcp", None)
        if mcp_instance is not None:
            async with mcp_instance.session_manager.run():
                yield
        else:
            yield
    finally:
        await scheduler.stop()


class _McpHttpGate:
    """Gate the mounted MCP HTTP app behind the live ``mcp_http_enabled`` setting.

    Also enforces a loopback-bind guard: an unauthenticated endpoint must never
    answer when the app is bound to a non-loopback host (e.g. 0.0.0.0). When
    closed it returns 404 so the endpoint simply appears absent. FastMCP's own
    Host-header allowlist (set in ``precursor_server``) is the second layer.
    """

    def __init__(self, inner: object, host: str) -> None:
        from precursor.backend.services.mcp.precursor_server import is_loopback_host

        self._inner = inner
        self._loopback = is_loopback_host(host)

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "http" and not await self._open():
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                }
            )
            await send({"type": "http.response.body", "body": b"Not Found"})
            return
        await self._inner(scope, receive, send)  # type: ignore[operator]

    async def _open(self) -> bool:
        if not self._loopback:
            return False
        from precursor.backend.db import SessionLocal
        from precursor.backend.services.app_settings import resolve_mcp_http_enabled

        async with SessionLocal() as session:
            return await resolve_mcp_http_enabled(session)


def _frontend_dist_dir() -> Path | None:
    """Locate the built SPA assets.

    Looks first inside the installed wheel (``precursor/frontend_dist``), then
    falls back to the source tree (``frontend/dist``) so ``uvicorn`` works
    from a checkout without ``pip install``.
    """
    try:
        resource = files("precursor").joinpath("frontend_dist")
        with as_file(resource) as path:
            if path.is_dir():
                return path
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


def create_app() -> FastAPI:
    cfg = get_settings()
    app = FastAPI(
        title="Precursor",
        version=__version__,
        description="Per-topic AI chat for work follow-up.",
        lifespan=lifespan,
    )

    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _client_id_middleware(request, call_next):  # type: ignore[no-untyped-def]
        # Tag this request with the originating window's id so events
        # published by handlers can be echo-suppressed in that window.
        from precursor.backend.services.events import set_current_client_id

        set_current_client_id(request.headers.get("x-client-id"))
        return await call_next(request)

    # API
    for r in (
        topics.router,
        chat.router,
        attachments.router,
        settings.router,
        github.router,
        mcp.router,
        llm.router,
        summary.router,
        issue.router,
        me.router,
        commands.router,
        skills.router,
        memories.router,
        events.router,
        workspaces.router,
        schedules.router,
        stt.router,
        raw.router,
        version.router,
    ):
        app.include_router(r)

    plugin_router = APIRouter(prefix="/api", tags=["plugins"])

    @plugin_router.get("/plugins")
    async def list_plugins() -> list[dict[str, object]]:
        return [
            {
                "id": ext.id,
                "kind": ext.kind,
                "slot": ext.slot,
                "title": ext.title,
                "config": ext.config,
            }
            for ext in get_registry().frontend_extensions
        ]

    @plugin_router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(plugin_router)

    # Outbound MCP over HTTP — serve the built-in "precursor" server's
    # streamable-HTTP endpoint at exactly /mcp. It runs in this process (so
    # post_message events reach the SPA) and inherits the app's bind. Access is
    # gated live by the mcp_http_enabled setting + a loopback guard; the session
    # manager's task group is started in the lifespan above (keyed off
    # app.state). A fresh instance per app keeps the run-once session manager
    # isolated.
    #
    # We reuse the SDK's own Route('/mcp') instead of app.mount("/mcp", …): a
    # mount only matches "/mcp/…", so a bare POST /mcp would fall through to the
    # SPA catch-all (GET-only) and 405. The exact Route matches /mcp for all
    # methods, with no trailing-slash redirect. It is appended before the SPA
    # fallback below.
    from precursor.backend.services.mcp.precursor_server import build_mcp

    precursor_mcp = build_mcp()
    _mcp_http_app = precursor_mcp.streamable_http_app()  # builds Route + manager
    app.state.precursor_http_mcp = precursor_mcp
    mcp_route = cast("Route", _mcp_http_app.routes[0])
    if getattr(mcp_route, "path", None) != "/mcp":  # defensive: SDK shape changed
        logger.warning(
            "Unexpected MCP route path %r; HTTP transport may misbehave",
            getattr(mcp_route, "path", None),
        )
    mcp_route.app = _McpHttpGate(mcp_route.app, cfg.host)
    app.router.routes.append(mcp_route)

    # SPA
    dist = _frontend_dist_dir()
    if dist is not None:
        assets_dir = dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        index_file = dist / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            return FileResponse(index_file)
    else:
        logger.warning(
            "Frontend dist not found — build it with `npm --prefix frontend run build` "
            "or run Vite on :5173 for development."
        )

    return app


app = create_app()
