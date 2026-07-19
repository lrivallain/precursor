"""Tests for the version endpoint + dynamic version resolution."""

from __future__ import annotations

from fastapi.testclient import TestClient

import precursor
from precursor import __version__
from precursor.backend.main import create_app
from precursor.backend.routers.version import _parse_local


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_resolver_queries_renamed_distribution(monkeypatch) -> None:
    """Guard against a silent regression if the distribution is renamed again.

    The PyPI distribution is ``precursor-ai`` (not ``precursor``), so the
    metadata lookup must use that name or it falls back to a stale build-time
    version.
    """
    assert precursor._DIST_NAME == "precursor-ai"

    queried: list[str] = []

    def fake_version(name: str) -> str:
        queried.append(name)
        return "2026.7.0"

    monkeypatch.setattr(precursor, "_pkg_version", fake_version)
    assert precursor._resolve_version() == "2026.7.0"
    assert queried == ["precursor-ai"]


def test_version_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == __version__
        assert "commit" in body
        assert "build_date" in body


def test_parse_local_clean_release() -> None:
    commit, build_date = _parse_local("2026.6.0")
    assert commit is None
    assert build_date is None


def test_parse_local_dev_version() -> None:
    commit, build_date = _parse_local("2026.6.1.dev3+g0f3ad9f.d20260615")
    assert commit == "0f3ad9f"
    assert build_date == "20260615"
