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
from precursor_kanban.sources import ProjectSource, parse_hidden, parse_source, parse_sources


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

    async def get_project_board(
        self, project_id: str, *, status_field_name: str = "Status"
    ) -> dict[str, Any]:
        return {
            "id": project_id,
            "title": "Roadmap",
            "url": None,
            "status_field": {"id": "FIELD_1", "name": "Status", "options": []},
            "items": [],
        }

    async def set_project_item_status(
        self, *, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> str:
        return item_id


@pytest.fixture()
def _github(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _repo(_session: Any) -> str:
        return "acme/app"

    async def _enabled(_session: Any) -> bool:
        return True

    async def _token(_session: Any) -> str:
        return "tok"

    monkeypatch.setattr(router_module, "ProjectsClient", _FakeClient)
    monkeypatch.setattr(router_module, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(router_module, "resolve_issue_associations_enabled", _enabled)
    monkeypatch.setattr(github_context, "resolve_github_token", _token)


@pytest.fixture()
def _github_no_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authenticated, feature on, but no repository configured."""

    async def _repo(_session: Any) -> str:
        return ""

    async def _enabled(_session: Any) -> bool:
        return True

    async def _token(_session: Any) -> str:
        return "tok"

    monkeypatch.setattr(router_module, "ProjectsClient", _FakeClient)
    monkeypatch.setattr(router_module, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(router_module, "resolve_issue_associations_enabled", _enabled)
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


def _set_settings(client: TestClient, **values: Any) -> None:
    r = client.put("/api/plugins/installed/kanban/settings", json=values)
    assert r.status_code == 200, r.text


def test_extra_owners_are_added_and_deduplicated(_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, [])
        assert [p["id"] for p in client.get("/api/github/projects").json()["projects"]] == [
            "PVT_acme_1"
        ]

        # "acme" re-lists the configured owner: its board 1 is already present and
        # must not appear twice, while board 2 is new.
        _set_sources(client, ["acme", "other"])
        ids = [p["id"] for p in client.get("/api/github/projects").json()["projects"]]
        assert ids == ["PVT_acme_1", "PVT_acme_2", "PVT_other_1", "PVT_other_2"]


def test_a_pinned_project_adds_only_that_board(_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["customer#7"])
        ids = [p["id"] for p in client.get("/api/github/projects").json()["projects"]]
        assert ids == ["PVT_acme_1", "PVT_customer_7"]


def test_an_unreachable_source_is_skipped_and_reported(_github: None) -> None:
    """A revoked or renamed source must not take the whole picker down.

    It is reported separately rather than silently dropped: an entry that
    resolves to no board has no row to right-click, so without this it would be
    invisible *and* impossible to remove now that the settings page is gone.
    """
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["broken", "other", "customer#99"])
        r = client.get("/api/github/projects")
        assert r.status_code == 200
        body = r.json()
        assert [p["id"] for p in body["projects"]] == [
            "PVT_acme_1",
            "PVT_other_1",
            "PVT_other_2",
        ]
        assert body["unresolved"] == [
            {"ref": "broken", "kind": "account"},
            {"ref": "customer#99", "kind": "pinned"},
        ]


def test_projects_carry_their_owner(_github: None) -> None:
    """Two boards called "Roadmap" are only distinguishable by account."""
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["other"])
        owners = {p["owner"] for p in client.get("/api/github/projects").json()["projects"]}
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
        assert [p["id"] for p in r.json()["projects"]] == [
            "PVT_acme_1",
            "PVT_other_1",
            "PVT_other_2",
        ]


def test_the_cap_counts_valid_sources_not_raw_entries() -> None:
    """Junk ahead of a good entry must not starve it out of the list."""
    assert parse_sources(["!!!"] * 30 + ["acme"]) == [ProjectSource("acme")]


def test_a_project_url_owner_is_validated_like_any_other() -> None:
    assert parse_source("https://github.com/orgs/a?z=1/projects/2") is None


# --- Provenance -------------------------------------------------------------
# The picker's context menu can only offer "stop tracking" when it knows which
# settings entry produced a board, and how many boards that entry brought.


def test_parse_source_keeps_the_raw_entry_without_it_affecting_identity() -> None:
    """Two spellings are the same source, but delete different array entries."""
    url = parse_source("https://github.com/orgs/acme/projects/4")
    short = parse_source("acme#4")
    assert url == short  # `raw` is provenance, not identity
    assert url is not None and short is not None
    assert url.raw == "https://github.com/orgs/acme/projects/4"
    assert short.raw == "acme#4"


def test_projects_report_where_they_came_from(_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["other", "customer#7"])
        by_id = {p["id"]: p for p in client.get("/api/github/projects").json()["projects"]}

    # The configured owner's board is implicit: nothing to stop tracking.
    assert by_id["PVT_acme_1"]["source"] == "repo"
    assert by_id["PVT_acme_1"]["source_ref"] is None
    # An account source brought two boards, both pointing back at one entry.
    assert by_id["PVT_other_1"]["source"] == "account"
    assert by_id["PVT_other_2"]["source_ref"] == "other"
    # A pinned source brought exactly one.
    assert by_id["PVT_customer_7"]["source"] == "pinned"
    assert by_id["PVT_customer_7"]["source_ref"] == "customer#7"


def test_source_ref_is_the_entry_as_typed(_github: None) -> None:
    """Removing a source is an array filter, so the ref must match verbatim."""
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["https://github.com/orgs/customer/projects/7"])
        found = {p["id"]: p for p in client.get("/api/github/projects").json()["projects"]}
    assert found["PVT_customer_7"]["source_ref"] == "https://github.com/orgs/customer/projects/7"


def test_a_redundant_source_does_not_override_repo_provenance(_github: None) -> None:
    """Pinning a board the configured owner already provides changes nothing.

    It adds no row to the picker, so there is nothing to right-click; the entry
    stays removable from the settings page.
    """
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["acme#1"])
        found = [
            p
            for p in client.get("/api/github/projects").json()["projects"]
            if p["id"] == "PVT_acme_1"
        ]
    assert len(found) == 1
    assert found[0]["source"] == "repo"


# --- Hidden boards ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["acme#1"], {"acme#1"}),
        (["ACME#1"], {"acme#1"}),  # owners are case-insensitive
        (["https://github.com/orgs/acme/projects/1"], {"acme#1"}),
        (["acme#1", "acme#1"], {"acme#1"}),
        # An accountwide entry names no single board, so it is not a hide.
        (["acme"], set()),
        (["!!!", 42, None], set()),
    ],
)
def test_parse_hidden_canonicalises_and_ignores_nonsense(
    raw: list[Any], expected: set[str]
) -> None:
    assert parse_hidden(raw) == expected


def test_parse_hidden_tolerates_a_non_list() -> None:
    assert parse_hidden({"nope": True}) == set()
    assert parse_hidden(None) == set()


def _hidden_flags(client: TestClient) -> dict[str, bool]:
    return {p["id"]: p["hidden"] for p in client.get("/api/github/projects").json()["projects"]}


def test_hiding_flags_a_board_rather_than_dropping_it(_github: None) -> None:
    """Hidden boards stay in the payload, marked.

    The picker is the only place to unhide one, so omitting them would make
    hiding a one-way door.
    """
    app = create_app()
    with TestClient(app) as client:
        _set_settings(client, project_sources=["other"], hidden_projects=["other#1"])
        flags = _hidden_flags(client)
    assert flags == {"PVT_acme_1": False, "PVT_other_1": True, "PVT_other_2": False}


def test_a_board_from_the_configured_owner_can_be_hidden(_github: None) -> None:
    """The whole point of the hidden list: no source produced this board."""
    app = create_app()
    with TestClient(app) as client:
        _set_settings(client, project_sources=[], hidden_projects=["acme#1"])
        flags = _hidden_flags(client)
    assert flags == {"PVT_acme_1": True}


def test_hiding_survives_a_source_being_re_added(_github: None) -> None:
    """Hidden is applied last, to the merged listing."""
    app = create_app()
    with TestClient(app) as client:
        _set_settings(client, project_sources=["other"], hidden_projects=["other#2"])
        flags = _hidden_flags(client)
    assert flags["PVT_other_2"] is True


def test_a_malformed_hidden_entry_cannot_break_the_listing(_github: None) -> None:
    """Same guard as sources: the hidden list is user input too."""
    app = create_app()
    with TestClient(app) as client:
        _set_settings(client, project_sources=["other"], hidden_projects=["acme#\u00b2", "other#1"])
        r = client.get("/api/github/projects")
        assert r.status_code == 200, r.text
        flags = {p["id"]: p["hidden"] for p in r.json()["projects"]}
    assert flags == {"PVT_acme_1": False, "PVT_other_1": True, "PVT_other_2": False}


# --- No configured repository ----------------------------------------------
# The repo is only ever read for its owner (`list_repo_projects` discards the
# name), so it is one way of saying "list this account's boards" — equivalent to
# adding that account as a source, and not a precondition for anything.


def test_sources_alone_are_enough(_github_no_repo: None) -> None:
    """A token plus a configured project is a complete, working setup."""
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, ["customer#7"])
        r = client.get("/api/github/projects")
        assert r.status_code == 200, r.text
        assert [p["id"] for p in r.json()["projects"]] == ["PVT_customer_7"]


def test_no_repo_and_no_sources_is_empty_not_an_error(_github_no_repo: None) -> None:
    """Nothing tracked is an empty board, not a failure."""
    app = create_app()
    with TestClient(app) as client:
        _set_sources(client, [])
        r = client.get("/api/github/projects")
        assert r.status_code == 200, r.text
        assert r.json() == {"projects": [], "unresolved": []}


def test_a_board_is_readable_without_a_configured_repo(_github_no_repo: None) -> None:
    """Project ids are global GitHub node ids; the repo never resolved them.

    The endpoint used to demand a repo and then discard it, which blocked
    reading a board the token could fetch perfectly well.
    """
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects/PVT_customer_7/board")
        assert r.status_code == 200, r.text


def test_moving_a_card_without_a_configured_repo(_github_no_repo: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/github/projects/PVT_1/items/ITEM_1/status",
            json={"field_id": "FIELD_1", "option_id": "OPT_DONE"},
        )
        assert r.status_code == 200, r.text


def test_the_feature_switch_still_gates_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping the repo requirement must not drop the GitHub master switch."""

    async def _disabled(_session: Any) -> bool:
        return False

    monkeypatch.setattr(router_module, "resolve_issue_associations_enabled", _disabled)
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/github/projects").status_code == 403
        assert client.get("/api/github/projects/PVT_1/board").status_code == 403
        assert (
            client.post(
                "/api/github/projects/PVT_1/items/ITEM_1/status",
                json={"field_id": "F", "option_id": "O"},
            ).status_code
            == 403
        )


# --- Enterprise Managed User logins ----------------------------------------
# EMU accounts are named `<shortcode>_<enterprise>`, so their logins contain an
# underscore. Classic github.com accounts never do, and a login rule written
# from those alone silently rejects every EMU account — including the URL you
# get by copying a project straight out of the address bar.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("octocat_acme", ProjectSource("octocat_acme")),
        ("octocat_acme#1", ProjectSource("octocat_acme", 1)),
        ("https://github.com/octocat_acme", ProjectSource("octocat_acme")),
        (
            "https://github.com/users/octocat_acme/projects/1",
            ProjectSource("octocat_acme", 1),
        ),
        (
            "https://github.com/orgs/octocat_acme/projects/2",
            ProjectSource("octocat_acme", 2),
        ),
    ],
)
def test_emu_logins_are_accepted(raw: str, expected: ProjectSource) -> None:
    assert parse_source(raw) == expected


@pytest.mark.parametrize("raw", ["_leading", "trailing_", "-leading", "trailing-"])
def test_a_login_still_has_to_start_and_end_alphanumeric(raw: str) -> None:
    assert parse_source(raw) is None
