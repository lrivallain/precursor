"""Production start must rebuild a stale pre-built SPA bundle.

Running prod from a source checkout serves ``frontend/dist``. After a
``git pull`` that bundle can lag the sources, so the launcher rebuilds when the
built output is older than any frontend source input — while never introducing a
hard Node.js requirement (a wheel install with no source checkout must still
start). ``_frontend_is_stale`` is the pure, mtime-based predicate behind that
decision; these tests pin its edges with explicit ``os.utime`` mtimes to avoid
filesystem-timestamp flakiness.
"""

from __future__ import annotations

import os
from pathlib import Path

import precursor.backend.__main__ as launcher
from precursor.backend.__main__ import _ensure_frontend_built, _frontend_is_stale


def _touch(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (mtime, mtime))


def test_stale_when_dist_missing(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    _touch(frontend / "src" / "main.tsx", 1000.0)
    assert _frontend_is_stale(frontend, frontend / "dist") is True


def test_stale_when_index_html_missing(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    _touch(frontend / "src" / "main.tsx", 1000.0)
    _touch(dist / "assets" / "app.js", 2000.0)  # dist exists but has no index.html
    assert _frontend_is_stale(frontend, dist) is True


def test_stale_when_source_newer_than_dist(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    _touch(dist / "index.html", 1000.0)
    _touch(frontend / "src" / "main.tsx", 2000.0)  # source edited after last build
    assert _frontend_is_stale(frontend, dist) is True


def test_not_stale_when_dist_newer_than_all_sources(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    _touch(frontend / "src" / "main.tsx", 1000.0)
    _touch(frontend / "package.json", 1000.0)
    _touch(dist / "index.html", 2000.0)
    _touch(dist / "assets" / "app.js", 2000.0)
    assert _frontend_is_stale(frontend, dist) is False


def test_node_modules_and_dist_changes_are_ignored(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    _touch(frontend / "src" / "main.tsx", 1000.0)
    _touch(dist / "index.html", 2000.0)
    # A freshly-installed dependency and a dist artifact both newer than the
    # bundle's index.html must NOT count as changed source.
    _touch(frontend / "node_modules" / "pkg" / "index.js", 3000.0)
    _touch(dist / "assets" / "app.js", 3000.0)
    assert _frontend_is_stale(frontend, dist) is False


def test_ensure_built_rebuilds_when_stale(tmp_path: Path, monkeypatch) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    _touch(frontend / "package.json", 1000.0)
    _touch(dist / "index.html", 1000.0)
    _touch(frontend / "src" / "main.tsx", 2000.0)  # stale bundle

    monkeypatch.setattr(launcher, "_repo_root", lambda: tmp_path)
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(launcher.subprocess, "run", _fake_run)

    assert _ensure_frontend_built(rebuild_if_stale=True) is True
    assert calls, "expected a rebuild to be invoked for a stale bundle"
    assert calls[0][:1] == ["npm"]
    assert "build" in calls[0]


def test_ensure_built_skips_rebuild_when_fresh(tmp_path: Path, monkeypatch) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    _touch(frontend / "package.json", 1000.0)
    _touch(frontend / "src" / "main.tsx", 1000.0)
    _touch(dist / "index.html", 2000.0)

    monkeypatch.setattr(launcher, "_repo_root", lambda: tmp_path)

    def _boom(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("must not rebuild a fresh bundle")

    monkeypatch.setattr(launcher.subprocess, "run", _boom)

    assert _ensure_frontend_built(rebuild_if_stale=True) is True
