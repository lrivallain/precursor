"""Tests for the on-demand draw.io webapp install (extraction, hosting, safety)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import drawio as service


def _war(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return path


def test_extract_writes_the_webapp(tmp_path: Path) -> None:
    war = _war(
        tmp_path / "draw.war",
        {
            "index.html": "<html>drawio</html>",
            "js/app.min.js": "// app",
            "stencils/azure.xml": "<shapes/>",
        },
    )

    service._extract(war, tmp_path / "out")

    assert (tmp_path / "out" / "index.html").read_text() == "<html>drawio</html>"
    assert (tmp_path / "out" / "js" / "app.min.js").is_file()
    assert (tmp_path / "out" / "stencils" / "azure.xml").is_file()


def test_extract_skips_servlet_plumbing(tmp_path: Path) -> None:
    war = _war(
        tmp_path / "draw.war",
        {
            "index.html": "x",
            "WEB-INF/web.xml": "<web-app/>",
            "WEB-INF/lib/servlet.jar": "binary",
            "META-INF/MANIFEST.MF": "Manifest-Version: 1.0",
        },
    )

    service._extract(war, tmp_path / "out")

    assert not (tmp_path / "out" / "WEB-INF").exists()
    assert not (tmp_path / "out" / "META-INF").exists()


def test_extract_rejects_a_non_drawio_archive(tmp_path: Path) -> None:
    war = _war(tmp_path / "draw.war", {"readme.txt": "not the webapp"})

    with pytest.raises(ValueError, match=r"index\.html"):
        service._extract(war, tmp_path / "out")


def test_extract_rejects_zip_slip(tmp_path: Path) -> None:
    war = _war(tmp_path / "draw.war", {"index.html": "x", "../escaped.txt": "pwned"})

    with pytest.raises(ValueError, match="escapes"):
        service._extract(war, tmp_path / "out")

    assert not (tmp_path / "escaped.txt").exists()


def test_prune_other_versions_keeps_only_the_current_one(tmp_path: Path) -> None:
    for name in ("v1.0.0", "v2.0.0", "v3.0.0"):
        (tmp_path / name).mkdir()

    service._prune_other_versions(tmp_path, "v3.0.0")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["v3.0.0"]


def test_asset_path_stays_inside_the_install_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "install"
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text("x")
    (root / "js" / "app.js").write_text("y")
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setattr(service, "install_dir", lambda: root)

    assert service.asset_path("index.html") == root / "index.html"
    assert service.asset_path("js/app.js") == root / "js" / "app.js"
    # An empty path serves the editor entry point.
    assert service.asset_path("") == root / "index.html"
    assert service.asset_path("../secret.txt") is None
    assert service.asset_path("missing.js") is None


def test_status_and_assets_follow_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "install"
    monkeypatch.setattr(service, "install_dir", lambda: root)
    client = TestClient(create_app())

    before = client.get("/api/drawio/status")
    assert before.status_code == 200
    assert before.json()["installed"] is False
    assert client.get("/drawio/index.html").status_code == 404

    root.mkdir(parents=True)
    (root / "index.html").write_text("<html>drawio</html>")

    after = client.get("/api/drawio/status")
    assert after.json()["installed"] is True
    served = client.get("/drawio/index.html")
    assert served.status_code == 200
    assert served.text == "<html>drawio</html>"


def test_install_is_a_no_op_once_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "install"
    root.mkdir(parents=True)
    (root / "index.html").write_text("x")
    monkeypatch.setattr(service, "install_dir", lambda: root)
    client = TestClient(create_app())

    # No network call: an already-installed webapp short-circuits.
    body = client.post("/api/drawio/install").json()

    assert body["installed"] is True
    assert body["step"] == "idle"
