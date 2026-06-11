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

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from precursor.backend.config import get_settings
from precursor.backend.db import init_db
from precursor.backend.plugins import discover, get_registry
from precursor.backend.routers import chat, github, mcp, settings, topics

logger = logging.getLogger("precursor")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    discover(app)
    yield


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
        version="0.1.0",
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

    # API
    for r in (topics.router, chat.router, settings.router, github.router, mcp.router):
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
        return {"status": "ok"}

    app.include_router(plugin_router)

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
