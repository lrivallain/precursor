"""Unit tests for the workspace filesystem rename/move helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from precursor.backend.services import workspace_fs as fs


def test_rename_file(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")

    fs.rename(tmp_path, "a.md", "b.md")

    assert not (tmp_path / "a.md").exists()
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "hello"


def test_rename_folder_moves_children(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.txt").write_text("x", encoding="utf-8")

    fs.rename(tmp_path, "src", "dst")

    assert not (tmp_path / "src").exists()
    assert (tmp_path / "dst" / "note.txt").read_text(encoding="utf-8") == "x"


def test_rename_creates_missing_parents(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hi", encoding="utf-8")

    fs.rename(tmp_path, "a.md", "nested/deep/a.md")

    assert (tmp_path / "nested" / "deep" / "a.md").read_text(encoding="utf-8") == "hi"


def test_rename_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        fs.rename(tmp_path, "nope.md", "x.md")


def test_rename_existing_destination_raises(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")

    with pytest.raises(FileExistsError):
        fs.rename(tmp_path, "a.md", "b.md")


def test_rename_same_path_is_noop(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a", encoding="utf-8")

    fs.rename(tmp_path, "a.md", "a.md")

    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a"


def test_rename_rejects_traversal(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a", encoding="utf-8")

    with pytest.raises(fs.UnsafePathError):
        fs.rename(tmp_path, "a.md", "../escape.md")


def test_rename_rejects_folder_into_own_descendant(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x", encoding="utf-8")

    with pytest.raises(fs.UnsafePathError):
        fs.rename(tmp_path, "a", "a/b")

    # The folder must be left intact.
    assert (tmp_path / "a" / "f.txt").exists()


def test_disk_path_takes_the_on_disk_spelling(tmp_path: Path) -> None:
    (tmp_path / "customers" / "Club Med").mkdir(parents=True)
    (tmp_path / "customers" / "Club Med" / "dns.drawio").write_text("x")

    assert fs.disk_path(tmp_path, "Customers/club med/DNS.drawio") == (
        "customers/Club Med/dns.drawio"
    )


def test_disk_path_keeps_an_exact_match_and_a_missing_tail(tmp_path: Path) -> None:
    (tmp_path / "customers").mkdir()

    assert fs.disk_path(tmp_path, "customers/a.md") == "customers/a.md"
    assert fs.disk_path(tmp_path, "Customers/New/b.md") == "customers/New/b.md"
    assert fs.disk_path(tmp_path, "/customers//a.md") == "customers/a.md"


def test_disk_path_leaves_an_ambiguous_segment_as_given(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    try:
        (tmp_path / "Notes").mkdir()
    except FileExistsError:
        pytest.skip("case-insensitive filesystem: two names differing in case can't coexist")

    assert fs.disk_path(tmp_path, "NOTES/a.md") == "NOTES/a.md"
    assert fs.disk_path(tmp_path, "Notes/a.md") == "Notes/a.md"
