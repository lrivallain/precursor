"""Reading uv's install receipt.

The receipt is the only durable record of how a tool environment was requested,
and ``uv tool install`` rebuilds that environment from it — so a field dropped
here is a package uninstalled on the next update.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from precursor.backend import uv_receipt


def _write(tmp_path: Path, body: str) -> None:
    (tmp_path / "uv-receipt.toml").write_text(body, encoding="utf-8")


def test_the_host_keeps_its_extras_and_pinned_wheel(tmp_path: Path) -> None:
    """Both are load-bearing: the extras carry the tray, and the URL is the
    difference between staying on a nightly and dropping to the latest release."""
    url = "https://example.invalid/nightly/precursor_ai-2026.9-py3-none-any.whl"
    _write(
        tmp_path,
        f'[tool]\nrequirements = [{{ name = "precursor-ai", extras = ["tray"], url = "{url}" }}]\n',
    )
    host = uv_receipt.host(str(tmp_path))
    assert host is not None
    assert host.extras == ("tray",)
    assert host.as_argument() == f"precursor-ai[tray] @ {url}"


def test_a_specifier_survives_when_there_is_no_url(tmp_path: Path) -> None:
    _write(
        tmp_path,
        '[tool]\nrequirements = [{ name = "precursor-ai", specifier = ">=2026.9" }]\n',
    )
    host = uv_receipt.host(str(tmp_path))
    assert host is not None and host.as_argument() == "precursor-ai>=2026.9"


def test_siblings_are_everything_that_is_not_the_host(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "[tool]\nrequirements = [\n"
        '    { name = "precursor-ai", extras = ["tray"] },\n'
        '    { name = "precursor-kanban" },\n'
        '    { name = "precursor-notes", specifier = "==1.2" },\n'
        "]\n",
    )
    assert [s.as_argument() for s in uv_receipt.siblings(str(tmp_path))] == [
        "precursor-kanban",
        "precursor-notes==1.2",
    ]


def test_a_missing_or_corrupt_receipt_is_not_fatal(tmp_path: Path) -> None:
    """A source checkout has none, and a half-written one must not take an
    update or the plugins panel down with it."""
    assert uv_receipt.requirements(str(tmp_path / "nothing-here")) == ()
    _write(tmp_path, "this is not toml [[[")
    assert uv_receipt.requirements(str(tmp_path)) == ()
    assert uv_receipt.host(str(tmp_path)) is None


def test_junk_entries_are_skipped_rather_than_raising(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "[tool]\nrequirements = [\n"
        '    "not-a-table",\n'
        '    { specifier = "==1" },\n'
        '    { name = "precursor-kanban" },\n'
        "]\n",
    )
    assert [r.name for r in uv_receipt.requirements(str(tmp_path))] == ["precursor-kanban"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("precursor-kanban", "precursor-kanban"),
        ("precursor_kanban", "precursor-kanban"),
        ("precursor-kanban==1.2", "precursor-kanban"),
        ("precursor-kanban[extra] @ https://x.invalid/a.whl", "precursor-kanban"),
        # A wheel URL has to resolve to the same name as the bare requirement,
        # or a same-commit pin and the receipt entry install the plugin twice.
        ("https://x.invalid/nightly/precursor_kanban-2026.9-py3-none-any.whl", "precursor-kanban"),
    ],
)
def test_a_distribution_is_recognised_however_it_is_written(value: str, expected: str) -> None:
    assert uv_receipt.canonical_name(value) == expected
