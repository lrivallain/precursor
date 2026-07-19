"""In-app docs serving: the ``/docs/*`` static handler.

Production (and one-port ``precursor``) serves the VitePress site — pre-built
with base ``/docs/`` — from ``precursor/website_dist`` (wheel) or
``website/.vitepress/dist`` (source). The handler must resolve VitePress
cleanUrls (``/docs/guide`` → ``guide.html``), redirect the bare ``/docs`` to the
trailing-slash base, fall back to the site's ``404.html``, and degrade to a
helpful 404 when the docs simply aren't built.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import precursor.backend.main as main
from precursor.backend.main import create_app


def _seed_docs(root: Path) -> None:
    (root / "index.html").write_text("<h1>Docs home</h1>")
    (root / "404.html").write_text("<h1>Not found</h1>")
    (root / "guide").mkdir()
    (root / "guide" / "introduction.html").write_text("<h1>Introduction</h1>")
    (root / "assets").mkdir()
    (root / "assets" / "app.js").write_text("console.log('docs')")


@pytest.fixture
def docs_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dist = tmp_path / "website_dist"
    dist.mkdir()
    _seed_docs(dist)
    monkeypatch.setattr(main, "_website_dist_dir", lambda: dist)
    return TestClient(create_app())


def test_docs_root_redirects_to_trailing_slash(docs_client: TestClient) -> None:
    resp = docs_client.get("/docs", follow_redirects=False)
    assert resp.status_code in (307, 308)
    assert resp.headers["location"] == "/docs/"


def test_docs_index_served(docs_client: TestClient) -> None:
    resp = docs_client.get("/docs/")
    assert resp.status_code == 200
    assert "Docs home" in resp.text


def test_docs_clean_url_resolves_to_html(docs_client: TestClient) -> None:
    # /docs/guide/introduction → guide/introduction.html (VitePress cleanUrls).
    resp = docs_client.get("/docs/guide/introduction")
    assert resp.status_code == 200
    assert "Introduction" in resp.text


def test_docs_exact_asset_served(docs_client: TestClient) -> None:
    resp = docs_client.get("/docs/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_docs_unknown_path_serves_404_page(docs_client: TestClient) -> None:
    resp = docs_client.get("/docs/does/not/exist")
    assert resp.status_code == 404
    assert "Not found" in resp.text


def test_docs_missing_build_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_website_dist_dir", lambda: None)
    client = TestClient(create_app())
    resp = client.get("/docs/")
    assert resp.status_code == 404
    assert "not built" in resp.text.lower()


def test_api_docs_relocated_off_root_docs(docs_client: TestClient) -> None:
    # FastAPI's interactive API docs must live under /api so /docs is free for
    # the product site. A regression here would shadow the docs redirect.
    assert docs_client.get("/api/openapi.json").status_code == 200
    assert docs_client.get("/api/docs").status_code == 200
    resp = docs_client.get("/docs", follow_redirects=False)
    assert resp.status_code in (307, 308)
    assert resp.headers["location"] == "/docs/"
