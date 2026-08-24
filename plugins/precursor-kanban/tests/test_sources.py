"""Extra project sources — parsing, and how they merge into the listing.

The board defaults to the projects owned by the configured repo's account; these
cover the escape hatch that lets a user add someone else's.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import github_context
from precursor_kanban import router as router_module
from precursor_kanban.sources import ProjectSource, parse_source, parse_sources


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme-corp", ProjectSource("acme-corp")),
        ("  acme-corp  ", ProjectSource("acme-corp")),
        ("https://github.com/acme-corp", ProjectSource("acme-corp")),
        ("https://github.com/acme-corp/", ProjectSource("acme-corp")),
        ("acme-corp#4", ProjectSource("acme-corp", 4)),
        ("https://github.com/orgs/acme-corp/projects/4", ProjectSource("acme-corp", 4)),
        ("https://github.com/users/octocat/projects/12", ProjectSource("octocat", 12)),
        # A project URL with a tab/view suffix is what you get from the address
        # bar, so it has to resolve rather than being rejected as malformed.
        ("https://github.com/orgs/acme/projects/2/views/1", ProjectSource("acme", 2)),
    ],
)
def test_parse_source_accepts_the_forms_people_have_to_hand(
    raw: str, expected: ProjectSource
) -> None:
    assert parse_source(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a login!",
        "acme-corp#notanumber",
        "-leading-hyphen",
        "https://gitlab.com/acme/projects/1",
        "https://github.com/acme/repo/issues/1",
    ],
)
def test_parse_source_rejects_nonsense(raw: str) -> None:
    assert parse_source(raw) is None


def test_parse_sources_drops_bad_entries_and_duplicates() -> None:
    """One malformed entry costs the user that source, not the whole board."""
    parsed = parse_sources(["acme", "!!!", "acme", "ACME", "acme#4", "acme#4", 42, None])
    assert parsed == [ProjectSource("acme"), ProjectSource("acme", 4)]


def test_parse_sources_tolerates_a_non_list() -> None:
    assert parse_sources({"nope": True}) == []
    assert parse_sources(None) == []


class _FakeClient:
    """Serves one board per owner, plus a pinned one."""

    def __init__(self, *, token: str) -> None:
        self.token = token

    async def aclose(self) -> None:
        return None

    @staticmethod
    def _project(owner: str, number: int) -> dict[str, Any]:
        return {
            "id": f"PVT_{owner}_{number}",
            "number": number,
            "title": "Roadmap",
            "url": None,
            "closed": False,
            "short_description": None,
            "owner": owner,
        }

    async def list_repo_projects(self, repo: str) -> list[dict[str, Any]]:
        return [self._project("acme", 1)]

    async def list_owner_projects(self, owner: str) -> list[dict[str, Any]]:
        if owner == "broken":
            raise RuntimeError("no such account")
        # Overlaps the configured owner's board when owner == "acme".
        return [self._project(owner, 1), self._project(owner, 2)]

    async def get_owner_project(self, owner: str, number: int) -> dict[str, Any] | None:
        return None if number == 99 else self._project(owner, number)


@pytest.fixture()
def _github(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _repo(_session: Any) -> str:
        return "acme/app"

    async def _enabled(_session: Any) -> bool:
        return True

    async def _token(_session: Any) -> str:
        return "tok"

    monkeypatch.setattr(router_module, "ProjectsClient", _FakeClient)
    monkeypatch.setattr(github_context, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(github_context, "resolve_issue_associations_enabled", _enabled)
    monkeypatch.setattr(github_context, "resolve_github_token", _token)


@pytest.fixture(autouse=True)
def _clean_plugin_settings() -> Any:
    """Leave no stored sources behind.

    The test database is shared for the whole session, so a leftover blob here
    would surface as a mystery failure in an unrelated module's tests.
    """
    yield
    import asyncio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting
    from precursor.backend.plugins.settings import settings_key

    async def _clear() -> None:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, settings_key("kanban"))
            if row is not None:
                await session.delete(row)
                await session.commit()

    asyncio.run(_clear())


def _set_sources(client: TestClient, sources: list[str]) -> None:
    r = client.put("/api/plugins/installed/kanban/settings", json={"project_sources": sources})
    assert r.status_code == 200, r.text


def test_extra_owners_are_added_and_deduplicated(_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, [])
        assert [p["id"] for p in client.get("/api/github/projects").json()] == ["PVT_acme_1"]

        # "acme" re-lists the configured owner: its board 1 is already present and
        # must not appear twice, while board 2 is new.
        _set_sources(client, ["acme", "other"])
        ids = [p["id"] for p in client.get("/api/github/projects").json()]
        assert ids == ["PVT_acme_1", "PVT_acme_2", "PVT_other_1", "PVT_other_2"]


def test_a_pinned_project_adds_only_that_board(_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["customer#7"])
        ids = [p["id"] for p in client.get("/api/github/projects").json()]
        assert ids == ["PVT_acme_1", "PVT_customer_7"]


def test_an_unreachable_source_is_skipped_not_fatal(_github: None) -> None:
    """A revoked or renamed source must not take the whole picker down."""
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["broken", "other", "customer#99"])
        r = client.get("/api/github/projects")
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert ids == ["PVT_acme_1", "PVT_other_1", "PVT_other_2"]


def test_projects_carry_their_owner(_github: None) -> None:
    """Two boards called "Roadmap" are only distinguishable by account."""
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["other"])
        owners = {p["owner"] for p in client.get("/api/github/projects").json()}
        assert owners == {"acme", "other"}


@pytest.mark.parametrize("raw", ["acme#\u00b2", "acme#\u2083", "acme#1\u00b2"])
def test_superscript_digits_do_not_crash_the_parser(raw: str) -> None:
    """`str.isdigit()` accepts these; `int()` refuses them.

    Getting that wrong let a value typed into the settings UI raise out of
    `configured_sources` and 500 `GET /api/github/projects` — the endpoint the
    whole board depends on — until the user found and removed it.
    """
    assert parse_source(raw) is None


def test_a_malformed_source_cannot_break_the_listing(_github: None) -> None:
    """End-to-end guard for the same bug: settings are user input."""
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["acme#\u00b2", "other"])
        r = client.get("/api/github/projects")
        assert r.status_code == 200, r.text
        assert [p["id"] for p in r.json()] == ["PVT_acme_1", "PVT_other_1", "PVT_other_2"]


def test_the_cap_counts_valid_sources_not_raw_entries() -> None:
    """Junk ahead of a good entry must not starve it out of the list."""
    assert parse_sources(["!!!"] * 30 + ["acme"]) == [ProjectSource("acme")]


def test_a_project_url_owner_is_validated_like_any_other() -> None:
    assert parse_source("https://github.com/orgs/a?z=1/projects/2") is None
