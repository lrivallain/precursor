"""Smoke tests for the FastAPI app — ensures imports + lifespan wire up cleanly."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_create_app_and_health() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert isinstance(body.get("version"), str) and body["version"]


def test_topics_empty_tree() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/topics/tree")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_log_config_unifies_format() -> None:
    import logging
    import re

    from precursor.backend.logging_config import UTCFormatter, build_log_config

    cfg = build_log_config("debug", color=False)
    # uvicorn + noisy third-party loggers route through the single root handler.
    assert cfg["root"]["handlers"] == ["default"]
    # Root level follows the app log_level (so precursor.* honours debug)...
    assert cfg["root"]["level"] == "DEBUG"
    # ...but noisy deps stay pinned regardless, so app DEBUG never unleashes
    # per-statement library spam.
    assert cfg["loggers"]["aiosqlite"]["level"] == "WARNING"
    assert cfg["loggers"]["sqlalchemy.engine"]["level"] == "WARNING"
    assert cfg["loggers"]["sse_starlette.sse"]["level"] == "INFO"
    for name in ("uvicorn", "uvicorn.access", "mcp", "httpx", "watchfiles"):
        assert cfg["loggers"][name]["handlers"] == []
        assert cfg["loggers"][name]["propagate"] is True
    # Keeps import-time module loggers alive so they propagate to root.
    assert cfg["disable_existing_loggers"] is False

    record = logging.LogRecord(
        "precursor.backend.services.scheduler",
        logging.INFO,
        __file__,
        1,
        "Scheduler started",
        None,
        None,
    )
    line = UTCFormatter(color=False).format(record)
    timestamp = line.split(" ", 1)[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp)
    assert "INFO" in line
    assert "precursor.backend.services.scheduler" in line
    assert line.endswith("Scheduler started")
    # Plain mode emits no ANSI escapes; colour mode wraps the level.
    assert "\033[" not in line
    assert "\033[" in UTCFormatter(color=True).format(record)
