"""Deleting a folder in a workspace: its contents go, nothing outside it does.

The Files section's folder delete is recursive, so the guards matter more than
the happy path: never the workspace's own folder, never ``.git``, nothing
outside the workspace, never a symlink's target, and never the folder holding
the agent and workflow definitions.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import workspace_fs as fs


def _tree(root: Path) -> None:
    (root / "notes/drafts").mkdir(parents=True)
    (root / "notes/a.md").write_text("a", encoding="utf-8")
    (root / "notes/drafts/b.md").write_text("b", encoding="utf-8")
    (root / "keep.md").write_text("k", encoding="utf-8")


def test_a_folder_goes_with_everything_in_it(tmp_path: Path) -> None:
    _tree(tmp_path)
    assert fs.delete_dir(tmp_path, "notes") == 2
    assert not (tmp_path / "notes").exists()
    assert (tmp_path / "keep.md").read_text(encoding="utf-8") == "k"


def test_an_empty_folder_can_be_deleted(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert fs.delete_dir(tmp_path, "empty/") == 0
    assert not (tmp_path / "empty").exists()


@pytest.mark.parametrize("rel", ["", ".", "/", "./"])
def test_the_workspace_folder_itself_is_refused(tmp_path: Path, rel: str) -> None:
    _tree(tmp_path)
    with pytest.raises(fs.UnsafePathError):
        fs.delete_dir(tmp_path, rel)
    assert (tmp_path / "keep.md").exists()


@pytest.mark.parametrize("rel", ["../outside", "notes/../../outside", ".git", ".git/hooks"])
def test_escapes_and_git_are_refused(tmp_path: Path, rel: str) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".git/hooks").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    with pytest.raises(fs.UnsafePathError):
        fs.delete_dir(root, rel)
    assert (tmp_path / "outside").is_dir() and (root / ".git/hooks").is_dir()


def test_missing_and_file_paths_are_told_apart(tmp_path: Path) -> None:
    _tree(tmp_path)
    with pytest.raises(FileNotFoundError):
        fs.delete_dir(tmp_path, "nope")
    with pytest.raises(NotADirectoryError):
        fs.delete_dir(tmp_path, "keep.md")


def test_a_symlinked_folder_loses_only_the_link(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "real"
    real.mkdir()
    (real / "x.md").write_text("x", encoding="utf-8")
    (root / "link").symlink_to(real, target_is_directory=True)
    fs.delete_dir(root, "link")
    assert not (root / "link").exists() and (real / "x.md").exists()


def test_links_inside_a_deleted_folder_leave_their_targets(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "doomed").mkdir(parents=True)
    precious = root / "precious"
    precious.mkdir()
    (precious / "x.md").write_text("x", encoding="utf-8")
    (root / "doomed/link").symlink_to(precious, target_is_directory=True)
    fs.delete_dir(root, "doomed")
    assert not (root / "doomed").exists() and (precious / "x.md").exists()


# --- API ----------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _local_workspace(client: TestClient) -> tuple[int, Path]:
    from precursor.backend.config import get_settings

    resp = client.post(
        "/api/workspaces", json={"name": f"Folders {uuid.uuid4().hex[:6]}", "kind": "local"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], Path(get_settings().workspaces_dir) / body["slug"]


def test_the_api_deletes_a_folder(client: TestClient) -> None:
    ws_id, root = _local_workspace(client)
    _tree(root)
    resp = client.delete(f"/api/workspaces/{ws_id}/folder", params={"path": "notes"})
    assert resp.status_code == 204, resp.text
    paths = [f["path"] for f in client.get(f"/api/workspaces/{ws_id}/files").json()]
    assert paths == ["keep.md"]
    assert (
        client.delete(f"/api/workspaces/{ws_id}/folder", params={"path": "notes"}).status_code
        == 404
    )
    assert (
        client.delete(f"/api/workspaces/{ws_id}/folder", params={"path": "../x"}).status_code == 400
    )
    assert client.delete(f"/api/workspaces/{ws_id}/folder", params={"path": ""}).status_code == 400
    client.delete(f"/api/workspaces/{ws_id}")


def test_the_folder_holding_the_definitions_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.config import get_settings

    ws_id, root = _local_workspace(client)
    (root / "defs/agents").mkdir(parents=True)
    (root / "defs/agents/a.agent.yaml").write_text("kind: agent\n", encoding="utf-8")
    (root / "other").mkdir()
    monkeypatch.setattr(get_settings(), "definitions_dir", str(root / "defs"))
    # The definitions folder itself, and any folder containing it.
    assert (
        client.delete(f"/api/workspaces/{ws_id}/folder", params={"path": "defs"}).status_code == 409
    )
    # A folder inside it is fine (with a warning in the app), and so is any other.
    assert (
        client.delete(f"/api/workspaces/{ws_id}/folder", params={"path": "defs/agents"}).status_code
        == 204
    )
    assert (
        client.delete(f"/api/workspaces/{ws_id}/folder", params={"path": "other"}).status_code
        == 204
    )
    assert (root / "defs").is_dir()
