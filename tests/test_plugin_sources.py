"""Plugin sources: recognising where a plugin comes from, listing its releases,
and turning a choice into the requirement handed to the installer.

The last part is the one with teeth — whatever it returns ends up as an
installer argument — so the rules for pinning, floors and GitHub wheels are
spelled out here rather than left to the router tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from precursor.backend import config
from precursor.backend.plugins import sources

WHEEL_92 = (
    "https://github.com/acme/precursor-notes/releases/download/v2.0.0/"
    "precursor_notes-2.0.0-py3-none-any.whl"
)


@pytest.fixture(autouse=True)
def _clean() -> Any:
    sources.invalidate()
    config.get_settings.cache_clear()
    yield
    sources.invalidate()
    config.get_settings.cache_clear()


# --- recognising a source ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/acme/precursor-notes", "acme/precursor-notes"),
        ("https://github.com/acme/precursor-notes/", "acme/precursor-notes"),
        ("https://github.com/acme/precursor-notes.git", "acme/precursor-notes"),
        ("github.com/acme/precursor-notes", "acme/precursor-notes"),
        ("https://www.github.com/acme/precursor.notes", "acme/precursor.notes"),
        ("https://github.com/acme/precursor-notes/releases/tag/v1", "acme/precursor-notes"),
        ("  https://github.com/acme/precursor-notes  ", "acme/precursor-notes"),
    ],
)
def test_github_repository_links_are_recognised(text: str, expected: str) -> None:
    assert sources.parse_github_repository(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "precursor-notes",
        "acme/precursor-notes",  # ambiguous without the host
        "https://gitlab.com/acme/precursor-notes",
        "https://github.com.evil.example/acme/precursor-notes",
        "https://user@github.com/acme/precursor-notes",
        "https://github.com:8443/acme/precursor-notes",
        "ftp://github.com/acme/precursor-notes",
        "https://github.com/acme",
        "https://github.com/-acme/precursor-notes",
        "https://github.com/acme/..",
        "",
    ],
)
def test_anything_else_is_not_a_github_repository(text: str) -> None:
    assert sources.parse_github_repository(text) is None


def test_a_spec_is_a_repository_a_name_or_neither() -> None:
    assert sources.classify("https://github.com/acme/precursor-notes") == sources.Source(
        "github", repository="acme/precursor-notes"
    )
    assert sources.classify("Precursor_Notes") == sources.Source(
        "pypi", distribution="precursor-notes"
    )
    # Free-form requirements are passed through, not second-guessed.
    assert sources.classify("precursor-notes>=2") is None
    assert sources.classify("precursor-notes @ https://example.invalid/x.whl") is None


def test_release_wheel_urls_and_filenames_are_parsed() -> None:
    assert sources.github_release_asset(WHEEL_92) == (
        "acme/precursor-notes",
        "v2.0.0",
        "precursor_notes-2.0.0-py3-none-any.whl",
    )
    assert sources.github_release_asset("https://example.invalid/x-1-py3-none-any.whl") is None
    assert sources.parse_wheel_filename("precursor_notes-2.0.0-py3-none-any.whl") == (
        "precursor-notes",
        "2.0.0",
    )
    assert sources.parse_wheel_filename("precursor_notes-2.0.0.tar.gz") is None


# --- how an installed plugin got here ---------------------------------------


def _receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *entries: str) -> None:
    body = "[tool]\nrequirements = [\n" + "".join(f"    {e},\n" for e in entries) + "]\n"
    (tmp_path / "uv-receipt.toml").write_text(body, encoding="utf-8")
    monkeypatch.setattr(sources.uv_receipt.sys, "prefix", str(tmp_path))


def test_the_receipt_says_where_a_plugin_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _receipt(
        tmp_path,
        monkeypatch,
        '{ name = "precursor-ai" }',
        f'{{ name = "precursor-notes", url = "{WHEEL_92}" }}',
        '{ name = "precursor_kanban", specifier = "==2026.9.1" }',
        '{ name = "precursor-mail", url = "https://example.invalid/mail-1-py3-none-any.whl" }',
    )
    assert sources.installed_source("precursor-notes") == sources.Source(
        "github", "precursor-notes", repository="acme/precursor-notes"
    )
    kanban = sources.installed_source("precursor-kanban")
    assert kanban.kind == "pypi" and kanban.pinned and kanban.specifier == "==2026.9.1"
    # An arbitrary URL has no releases to list.
    assert sources.installed_source("precursor-mail").kind == "direct"


class _Dist:
    def __init__(self, direct_url: dict[str, Any] | None) -> None:
        self._raw = json.dumps(direct_url) if direct_url is not None else None

    def read_text(self, name: str) -> str | None:
        return self._raw if name == "direct_url.json" else None


@pytest.mark.parametrize(
    ("direct_url", "kind", "repository"),
    [
        (None, "pypi", None),
        ({"url": WHEEL_92, "archive_info": {}}, "github", "acme/precursor-notes"),
        (
            {"url": "https://github.com/acme/precursor-notes.git", "vcs_info": {"vcs": "git"}},
            "github",
            "acme/precursor-notes",
        ),
        ({"url": "file:///src/notes", "dir_info": {"editable": True}}, "direct", None),
    ],
)
def test_other_installers_are_read_from_direct_url_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direct_url: dict[str, Any] | None,
    kind: str,
    repository: str | None,
) -> None:
    """Without a uv receipt, PEP 610's record is what pip and `uv pip` leave."""
    monkeypatch.setattr(sources.uv_receipt.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(sources.metadata, "distribution", lambda _name: _Dist(direct_url))
    source = sources.installed_source("precursor-notes")
    assert (source.kind, source.repository) == (kind, repository)


# --- listing releases -------------------------------------------------------


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(recording)
        return original(*args, **kwargs)

    monkeypatch.setattr(sources.httpx, "AsyncClient", factory)
    return seen


PYPI_PAYLOAD = {
    "info": {"name": "precursor-notes", "version": "2.0.0"},
    "releases": {
        "1.0.0": [{"upload_time_iso_8601": "2026-01-01T00:00:00Z"}],
        "2.0.0": [{"upload_time_iso_8601": "2026-03-01T00:00:00Z"}],
        "2.1.0rc1": [{"upload_time_iso_8601": "2026-04-01T00:00:00Z"}],
        "1.5.0": [{"upload_time_iso_8601": "2026-02-01T00:00:00Z", "yanked": True}],
        "0.1.0": [],
    },
}


def _gh_release(tag: str, version: str, *, prerelease: bool = False, **extra: Any) -> dict:
    wheel = f"precursor_notes-{version}-py3-none-any.whl"
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": extra.get("draft", False),
        "published_at": extra.get("published_at", f"2026-0{version[0]}-01T00:00:00Z"),
        "assets": extra.get(
            "assets",
            [
                {
                    "name": wheel,
                    "browser_download_url": (
                        f"https://github.com/acme/precursor-notes/releases/download/{tag}/{wheel}"
                    ),
                },
                {"name": f"precursor_notes-{version}.tar.gz", "browser_download_url": "x"},
            ],
        ),
    }


GITHUB_PAYLOAD = [
    _gh_release("v3.0.0b1", "3.0.0b1", prerelease=True),
    _gh_release("v2.0.0", "2.0.0"),
    _gh_release("v1.0.0", "1.0.0"),
    _gh_release("nightly", "9.9.9", draft=True),
    _gh_release("docs", "0.0.0", assets=[]),
]


async def test_pypi_releases_skip_yanked_and_empty_and_prefer_pypis_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=PYPI_PAYLOAD))
    releases = await sources.list_releases(sources.Source("pypi", "precursor-notes"))

    assert str(seen[0].url) == "https://pypi.org/pypi/precursor-notes/json"
    assert [r.version for r in releases.releases] == ["2.1.0rc1", "2.0.0", "1.0.0"]
    assert releases.releases[0].prerelease is True
    assert releases.latest is not None and releases.latest.version == "2.0.0"


async def test_github_releases_come_from_release_wheels_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=GITHUB_PAYLOAD))
    releases = await sources.list_releases(
        sources.Source("github", repository="acme/precursor-notes"), token="t"
    )

    assert seen[0].url.host == "api.github.com"
    assert seen[0].headers["authorization"] == "Bearer t"
    # Drafts and releases without a wheel are not installable.
    assert [r.version for r in releases.releases] == ["3.0.0b1", "2.0.0", "1.0.0"]
    assert releases.source.distribution == "precursor-notes"
    # A pre-release is listed, but "latest" means the newest stable one.
    assert releases.latest is not None and releases.latest.version == "2.0.0"
    assert releases.latest.wheel_url == WHEEL_92
    assert releases.find("v1.0.0") is releases.releases[2]


async def test_a_wheel_hosted_anywhere_but_a_github_release_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        _gh_release(
            "v2.0.0",
            "2.0.0",
            assets=[
                {
                    "name": "precursor_notes-2.0.0-py3-none-any.whl",
                    "browser_download_url": "https://evil.example/precursor_notes-2.0.0-py3-none-any.whl",
                }
            ],
        )
    ]
    _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=payload))
    with pytest.raises(sources.SourceError) as caught:
        await sources.list_releases(sources.Source("github", repository="acme/precursor-notes"))
    assert caught.value.status == 404


async def test_a_stale_token_does_not_hide_a_public_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers:
            return httpx.Response(401)
        return httpx.Response(200, json=GITHUB_PAYLOAD)

    seen = _patch_client(monkeypatch, handler)
    releases = await sources.list_releases(
        sources.Source("github", repository="acme/precursor-notes"), token="expired"
    )
    assert len(seen) == 2 and releases.latest is not None


@pytest.mark.parametrize(("status", "expected"), [(404, 404), (403, 502), (500, 502)])
async def test_lookup_failures_carry_a_status(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: int
) -> None:
    _patch_client(monkeypatch, lambda _r: httpx.Response(status))
    with pytest.raises(sources.SourceError) as caught:
        await sources.list_releases(sources.Source("github", repository="acme/precursor-notes"))
    assert caught.value.status == expected


async def test_lookups_are_cached_unless_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=PYPI_PAYLOAD))
    source = sources.Source("pypi", "precursor-notes")
    await sources.list_releases(source)
    await sources.list_releases(source)
    assert len(seen) == 1
    await sources.list_releases(source, refresh=True)
    assert len(seen) == 2


def test_newer_follows_the_sources_own_order() -> None:
    releases = sources.Releases(
        sources.Source("pypi", "precursor-notes"),
        (sources.Release("2.0.0"), sources.Release("1.10.0"), sources.Release("1.9.0")),
        sources.Release("2.0.0"),
    )
    assert sources.is_newer(releases, "1.9.0")
    assert sources.is_newer(releases, "v1.10.0")
    assert not sources.is_newer(releases, "2.0.0")
    assert not sources.is_newer(releases, None)
    # A local build the source never published: compare the numbers.
    assert sources.is_newer(releases, "1.9.1.dev3+gabc")
    assert not sources.is_newer(releases, "2.0.1.dev3+gabc")


# --- turning a choice into a requirement ------------------------------------


async def test_a_chosen_version_is_pinned_without_asking_anyone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming a release needs no lookup — and must work for a private index
    PyPI has never heard of."""
    seen = _patch_client(monkeypatch, lambda _r: httpx.Response(500))
    chosen = await sources.resolve("precursor-notes", "1.0.0")
    assert chosen.requirement == "precursor-notes==1.0.0"
    assert (chosen.version, chosen.kind) == ("1.0.0", "pypi")
    assert seen == []


async def test_latest_from_pypi_is_a_floor_not_a_bare_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this guards: "Latest (2.0.0)" installed as a bare name, so
    an index that lagged PyPI quietly installed an older release — built for an
    older MCP — and dragged core's dependencies down with it. A floor fails
    loudly instead, and unlike a pin it doesn't block the next upgrade."""
    _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=PYPI_PAYLOAD))
    latest = await sources.resolve("precursor-notes")
    assert latest.requirement == "precursor-notes>=2.0.0"
    assert latest.version == "2.0.0"


async def test_latest_falls_back_to_the_bare_name_when_pypi_cant_be_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, lambda _r: httpx.Response(503))
    assert (await sources.resolve("precursor-notes")).requirement == "precursor-notes"


async def test_a_repository_installs_a_release_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda _r: httpx.Response(200, json=GITHUB_PAYLOAD))

    latest = await sources.resolve("https://github.com/acme/precursor-notes")
    assert latest.requirement == f"precursor-notes @ {WHEEL_92}"
    older = await sources.resolve("github.com/acme/precursor-notes", "1.0.0")
    assert older.requirement.endswith("/v1.0.0/precursor_notes-1.0.0-py3-none-any.whl")
    by_tag = await sources.resolve("github.com/acme/precursor-notes", "v3.0.0b1")
    assert by_tag.version == "3.0.0b1"

    with pytest.raises(sources.SourceError) as caught:
        await sources.resolve("github.com/acme/precursor-notes", "4.0.0")
    assert caught.value.status == 404


@pytest.mark.parametrize("version", ["1.0; rm -rf /", "1.0 --index-url x", "-1", "a" * 80])
async def test_a_version_that_isnt_one_is_refused(version: str) -> None:
    with pytest.raises(sources.SourceError) as caught:
        await sources.resolve("precursor-notes", version)
    assert caught.value.status == 400


async def test_a_free_form_requirement_passes_through_but_takes_no_version() -> None:
    requirement = "precursor-notes[extra]>=2"
    assert (await sources.resolve(requirement)).requirement == requirement
    with pytest.raises(sources.SourceError):
        await sources.resolve(requirement, "2.0.0")
