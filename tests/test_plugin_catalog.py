"""The bundled plugin catalogue: parsing, validation, and the API it feeds.

Two things are being protected here.

**The supply chain.** A catalog entry's ``distribution`` is handed to an
installer, so it must be a bare PyPI name and nothing that can express a
location. If that check ever regressed, a merged pull request would become code
execution on every machine that opened the Plugins panel.

**The shipped data.** Every entry Precursor ships is re-validated strictly, so a
malformed submission fails CI rather than being silently skipped at runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.plugins import catalog as catalog_mod
from precursor.backend.plugins.catalog import (
    CatalogError,
    catalog_dir,
    load_catalog,
    normalize_distribution,
    parse_entry,
    read_raw_entries,
    split_frontmatter,
)

VALID = {
    "title": "My plugin",
    "description": "Does a thing.",
    "plugin": "my-plugin",
    "distribution": "precursor-my-plugin",
}


def _entry(**overrides: object) -> dict[str, object]:
    return {**VALID, **overrides}


# -- frontmatter ------------------------------------------------------------


def test_split_frontmatter_reads_the_leading_block() -> None:
    data = split_frontmatter("---\ntitle: Hi\nplugin: x\n---\n\n# Body\n")
    assert data == {"title": "Hi", "plugin": "x"}


def test_split_frontmatter_returns_none_without_a_block() -> None:
    assert split_frontmatter("# Just a heading\n") is None


def test_split_frontmatter_returns_none_when_unterminated() -> None:
    assert split_frontmatter("---\ntitle: Hi\n") is None


def test_split_frontmatter_rejects_invalid_yaml() -> None:
    with pytest.raises(CatalogError):
        split_frontmatter("---\n: : :\n\tbad\n---\n")


# -- validation -------------------------------------------------------------


def test_parse_entry_accepts_a_minimal_entry() -> None:
    entry = parse_entry("my-plugin", _entry())
    assert entry.id == "my-plugin"
    assert entry.distribution == "precursor-my-plugin"
    assert entry.summary == "Does a thing."
    assert entry.recommended is False
    assert entry.docs_path == "/docs/plugins/my-plugin"


@pytest.mark.parametrize(
    "distribution",
    [
        # The whole point: nothing that names a *location* may appear here.
        "pkg @ https://example.invalid/evil.whl",
        "https://example.invalid/evil.whl",
        "./local-package",
        "/etc/passwd",
        "git+https://example.invalid/repo.git",
        # Nor anything that isn't purely a name.
        "precursor-kanban[extra]",
        "precursor-kanban==1.0",
        "precursor-kanban; python_version>'3'",
        "two words",
        "-leading-dash",
        "trailing-dash-",
        "",
    ],
)
def test_parse_entry_rejects_anything_but_a_bare_pypi_name(distribution: str) -> None:
    with pytest.raises(CatalogError):
        parse_entry("my-plugin", _entry(distribution=distribution))


def test_the_name_patterns_do_not_stop_at_a_newline() -> None:
    """The regexes must be safe on their own, not only after a caller strips.

    Python's ``$`` also matches *before* a trailing newline, so ``"pkg\\n"``
    would pass a ``$``-anchored pattern. ``parse_entry`` happens to strip first
    and would never notice — which is exactly why this is pinned at the pattern
    level: a security check that depends on its caller sanitising the input is
    one refactor away from being no check at all.
    """
    assert not catalog_mod.DISTRIBUTION_RE.match("precursor-kanban\n")
    assert not catalog_mod.PLUGIN_ID_RE.match("kanban\n")
    assert catalog_mod.DISTRIBUTION_RE.match("precursor-kanban")
    assert catalog_mod.PLUGIN_ID_RE.match("kanban")


def test_parse_entry_requires_the_id_to_match_the_file_name() -> None:
    # Otherwise the catalogue links one plugin's card to another's docs page.
    with pytest.raises(CatalogError, match="must match the file name"):
        parse_entry("other", _entry())


@pytest.mark.parametrize("plugin_id", ["Upper", "with space", "under_score", "-dash", ""])
def test_parse_entry_rejects_an_unusable_plugin_id(plugin_id: str) -> None:
    with pytest.raises(CatalogError):
        parse_entry(plugin_id, _entry(plugin=plugin_id))


@pytest.mark.parametrize("field", ["title", "description", "plugin", "distribution"])
def test_parse_entry_requires_the_mandatory_fields(field: str) -> None:
    data = _entry()
    del data[field]
    with pytest.raises(CatalogError):
        parse_entry("my-plugin", data)


def test_parse_entry_rejects_a_non_https_homepage() -> None:
    with pytest.raises(CatalogError, match="https"):
        parse_entry("my-plugin", _entry(homepage="http://example.invalid"))


def test_parse_entry_rejects_unknown_contributions() -> None:
    with pytest.raises(CatalogError, match="unknown 'contributes'"):
        parse_entry("my-plugin", _entry(contributes=["section", "telepathy"]))


def test_parse_entry_rejects_a_string_where_a_list_belongs() -> None:
    with pytest.raises(CatalogError, match="list of strings"):
        parse_entry("my-plugin", _entry(tags="github"))


def test_parse_entry_rejects_a_non_boolean_recommendation() -> None:
    with pytest.raises(CatalogError, match="true or false"):
        parse_entry("my-plugin", _entry(recommended="yes"))


# -- loading ----------------------------------------------------------------


def _write(directory: Path, name: str, frontmatter: str, body: str = "Body.") -> None:
    (directory / name).write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def test_read_raw_entries_ignores_pages_without_a_distribution(tmp_path: Path) -> None:
    # This is what lets the index and the submission guide live alongside the
    # entries as ordinary pages.
    _write(tmp_path, "index.md", "title: Catalogue")
    _write(tmp_path, "kanban.md", "plugin: kanban\ndistribution: precursor-kanban")
    assert [raw.slug for raw in read_raw_entries(tmp_path)] == ["kanban"]


def test_load_catalog_skips_an_invalid_entry_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "good.md",
        "title: Good\ndescription: Fine.\nplugin: good\ndistribution: precursor-good",
    )
    _write(
        tmp_path,
        "bad.md",
        "title: Bad\ndescription: Nope.\nplugin: bad\n"
        "distribution: bad @ https://example.invalid/x.whl",
    )
    monkeypatch.setattr(catalog_mod, "catalog_dir", lambda: tmp_path)
    load_catalog.cache_clear()
    try:
        assert [e.id for e in load_catalog()] == ["good"]
    finally:
        load_catalog.cache_clear()


def test_load_catalog_drops_a_duplicate_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for slug in ("aaa", "bbb"):
        _write(
            tmp_path,
            f"{slug}.md",
            f"title: {slug}\ndescription: d\nplugin: {slug}\ndistribution: precursor-same",
        )
    monkeypatch.setattr(catalog_mod, "catalog_dir", lambda: tmp_path)
    load_catalog.cache_clear()
    try:
        assert [e.id for e in load_catalog()] == ["aaa"]
    finally:
        load_catalog.cache_clear()


def test_load_catalog_puts_recommended_entries_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "aaa.md", "title: Aaa\ndescription: d\nplugin: aaa\ndistribution: pkg-aaa")
    _write(
        tmp_path,
        "zzz.md",
        "title: Zzz\ndescription: d\nplugin: zzz\ndistribution: pkg-zzz\nrecommended: true",
    )
    monkeypatch.setattr(catalog_mod, "catalog_dir", lambda: tmp_path)
    load_catalog.cache_clear()
    try:
        assert [e.id for e in load_catalog()] == ["zzz", "aaa"]
    finally:
        load_catalog.cache_clear()


def test_load_catalog_is_empty_without_a_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_mod, "catalog_dir", lambda: None)
    load_catalog.cache_clear()
    try:
        assert load_catalog() == ()
    finally:
        load_catalog.cache_clear()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Precursor_Kanban", "precursor-kanban"),
        ("precursor.kanban", "precursor-kanban"),
        ("PRECURSOR--KANBAN", "precursor-kanban"),
    ],
)
def test_normalize_distribution_follows_pep_503(name: str, expected: str) -> None:
    assert normalize_distribution(name) == expected


# -- the entries Precursor actually ships -----------------------------------


def test_the_build_hook_ships_the_catalog_where_the_loader_looks() -> None:
    """The wheel destination is a two-place constant; pin the two together.

    If they drift, an installed Precursor finds no ``precursor/catalog``, falls
    back to a source tree that isn't there, and shows an *empty* catalogue — no
    error, no log, just a panel that quietly lost its Available list. Asserted
    against the hook's source text because importing it needs hatchling, which
    is a build-time dependency rather than a project one.
    """
    hook = (Path(__file__).resolve().parents[1] / "hatch_build.py").read_text(encoding="utf-8")
    source_tuple = ", ".join(f'"{part}"' for part in catalog_mod.SOURCE_DIR)
    assert f"({source_tuple})" in hook, f"the build hook should bundle {source_tuple}"
    assert f'"precursor/{catalog_mod.WHEEL_DIR}"' in hook, (
        f"the build hook should map it to precursor/{catalog_mod.WHEEL_DIR}"
    )


def test_the_shipped_catalog_is_valid() -> None:
    """Every bundled entry must pass strict validation.

    ``load_catalog`` deliberately skips a bad entry at runtime so one bad file
    can't take the panel down. That leniency must not become a way for a
    malformed submission to reach a release, so re-validate strictly here.
    """
    directory = catalog_dir()
    assert directory is not None, "the catalogue should ship with Precursor"
    raws = read_raw_entries(directory)
    assert raws, "the catalogue should not be empty"
    for raw in raws:
        parse_entry(raw.slug, raw.frontmatter)


def test_the_shipped_catalog_documents_every_entry() -> None:
    """An entry without prose is a card with nowhere to go."""
    directory = catalog_dir()
    assert directory is not None
    for raw in read_raw_entries(directory):
        text = (directory / f"{raw.slug}.md").read_text(encoding="utf-8")
        body = text.split("---", 2)[-1].strip()
        assert len(body) > 200, f"{raw.slug}: needs a real documentation page"


# -- the API ----------------------------------------------------------------


def test_catalog_endpoint_reports_installed_state() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/api/plugins/catalog")
        assert resp.status_code == 200
        entries = resp.json()
        assert entries, "the bundled catalogue should not be empty"
        for entry in entries:
            assert set(entry) >= {
                "id",
                "distribution",
                "title",
                "summary",
                "tags",
                "contributes",
                "recommended",
                "docs_path",
                "installed",
                "enabled",
                "installed_version",
            }
            # Nothing that could smuggle a location past the installer.
            assert " " not in entry["distribution"]
            assert "@" not in entry["distribution"]
            assert entry["docs_path"] == f"/docs/plugins/{entry['id']}"


def test_catalog_endpoint_needs_no_install_privileges() -> None:
    """Reading the catalogue executes nothing, so it isn't behind the gates."""
    with TestClient(create_app()) as client:
        assert client.get("/api/plugins/catalog").status_code == 200
