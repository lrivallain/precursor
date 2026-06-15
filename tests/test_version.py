"""Tests for the version endpoint + dynamic version resolution."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor import __version__
from precursor.backend.main import create_app
from precursor.backend.routers.version import _parse_local


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__


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
