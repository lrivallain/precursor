"""Live summary templates: definition files, the catalogue and the recap options.

A template is a ``*.summary.yaml`` in the definitions folder (or a built-in);
the Summary tab picks one and a language, both remembered for the next recap.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import meeting_summary
from precursor.backend.services.definitions import overlay
from precursor.backend.services.definitions.checker import build_report
from precursor.backend.services.definitions.loader import load_definitions
from precursor.backend.services.llm.one_shot import OneShotResult
from precursor.backend.services.summary_templates import build_catalog, builtin_templates

TEMPLATE = "kind: summary\nid: {id}\nname: {name}\nprompt: {prompt}\n"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- Files and the catalogue -------------------------------------------------


def test_builtin_templates_are_valid_and_standard_comes_first(tmp_path: Path) -> None:
    builtins = builtin_templates()
    assert {"standard", "executive-brief", "action-items", "detailed-minutes"} <= set(builtins)
    catalog = build_catalog(load_definitions(tmp_path))
    assert catalog.templates[0].id == "standard"
    assert all(t.path is None and t.builtin for t in catalog.templates)
    assert catalog.problems == []


def test_summary_files_are_definition_files(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "summaries/brief.summary.yaml",
        TEMPLATE.format(id="brief", name="Brief", prompt="Be brief."),
    )
    _write(tmp_path, "summaries/wrong.summary.yaml", "kind: agent\nid: wrong\ntitle: Wrong\n")
    report = build_report(load_definitions(tmp_path))
    files = {f.path: f for f in report.files}
    assert (
        files["summaries/brief.summary.yaml"].kind,
        files["summaries/brief.summary.yaml"].name,
    ) == (
        "summary",
        "Brief",
    )
    assert files["summaries/wrong.summary.yaml"].valid is False
    assert any(
        "*.summary.yaml file needs `kind: summary`" in i.message
        for i in report.issues
        if i.path == "summaries/wrong.summary.yaml"
    )


def test_a_file_adds_or_replaces_a_template(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "summaries/mine.summary.yaml",
        TEMPLATE.format(id="mine", name="Alpha", prompt="Mine."),
    )
    _write(
        tmp_path,
        "summaries/standard.summary.yaml",
        TEMPLATE.format(id="standard", name="House recap", prompt="Our way."),
    )
    catalog = build_catalog(load_definitions(tmp_path))
    standard = catalog.find("standard")
    assert standard is not None
    assert (standard.name, standard.prompt, standard.path, standard.builtin) == (
        "House recap",
        "Our way.",
        "summaries/standard.summary.yaml",
        True,
    )
    mine = catalog.find("mine")
    assert mine is not None and mine.path == "summaries/mine.summary.yaml" and not mine.builtin
    # The default stays first; the rest are by name.
    assert catalog.templates[0].id == "standard"
    assert catalog.templates[1].id == "action-items"
    assert catalog.templates[2].id == "mine"


def test_a_broken_file_is_reported_and_the_builtin_stays(tmp_path: Path) -> None:
    _write(
        tmp_path, "summaries/standard.summary.yaml", "kind: summary\nid: standard\nname: Broken\n"
    )
    _write(tmp_path, "summaries/a.summary.yaml", TEMPLATE.format(id="twin", name="A", prompt="a"))
    _write(tmp_path, "summaries/b.summary.yaml", TEMPLATE.format(id="twin", name="B", prompt="b"))
    catalog = build_catalog(load_definitions(tmp_path))
    standard = catalog.find("standard")
    assert standard is not None and standard.path is None
    assert catalog.find("twin") is None
    paths = {p.path for p in catalog.problems}
    assert paths == {
        "summaries/standard.summary.yaml",
        "summaries/a.summary.yaml",
        "summaries/b.summary.yaml",
    }


# --- API ---------------------------------------------------------------------


def _root() -> Path:
    return overlay.definitions_root()


async def _forget_last_used() -> None:
    from precursor.backend.db import SessionLocal, init_db
    from precursor.backend.models import AppSetting

    await init_db()
    async with SessionLocal() as session:
        for key in ("live_summary_template", "live_summary_language"):
            row = await session.get(AppSetting, key)
            if row is not None:
                await session.delete(row)
        await session.commit()


@pytest.fixture
async def defs_root() -> AsyncIterator[Path]:
    root = _root()
    shutil.rmtree(root, ignore_errors=True)
    overlay.invalidate()
    await _forget_last_used()
    yield root
    shutil.rmtree(root, ignore_errors=True)
    overlay.invalidate()
    await _forget_last_used()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    seen: list[str] = []

    async def _fake_complete_once(_session: Any, *, system: str, **_kwargs: Any) -> OneShotResult:
        seen.append(system)
        return OneShotResult(text="## Summary\nDone.", model="fake", usage=None)

    monkeypatch.setattr(meeting_summary, "complete_once", _fake_complete_once)
    yield seen


def test_catalog_lists_templates_languages_and_the_last_used(defs_root: Path) -> None:
    _write(
        defs_root,
        "summaries/mine.summary.yaml",
        TEMPLATE.format(id="mine", name="Mine", prompt="Mine."),
    )
    with TestClient(create_app()) as client:
        body = client.get("/api/live/summary-templates").json()
        ids = [t["id"] for t in body["templates"]]
        assert ids[0] == "standard" and "mine" in ids
        mine = next(t for t in body["templates"] if t["id"] == "mine")
        assert (mine["source"], mine["path"], mine["overrides_builtin"]) == (
            "file",
            "summaries/mine.summary.yaml",
            False,
        )
        assert {"code": "fr", "name": "French"} in body["languages"]
        assert (body["last_template"], body["last_language"]) == ("standard", "")
        assert body["folder"] == str(defs_root)

        # The picker's choice is remembered.
        assert (
            client.put(
                "/api/live/summary-templates/selection", json={"template": "mine", "language": "de"}
            ).status_code
            == 204
        )
        body = client.get("/api/live/summary-templates").json()
        assert (body["last_template"], body["last_language"]) == ("mine", "de")

        bad = client.put("/api/live/summary-templates/selection", json={"template": "nope"})
        assert bad.status_code == 400
        bad = client.put(
            "/api/live/summary-templates/selection", json={"template": "mine", "language": "xx"}
        )
        assert bad.status_code == 422

        # A last-used template whose file is gone falls back to the default.
        (defs_root / "summaries/mine.summary.yaml").unlink()
        overlay.invalidate()
        assert client.get("/api/live/summary-templates").json()["last_template"] == "standard"


def test_the_recap_uses_the_template_and_language_and_remembers_them(
    defs_root: Path, prompts: list[str]
) -> None:
    _write(
        defs_root,
        "summaries/haiku.summary.yaml",
        TEMPLATE.format(id="haiku", name="Haiku", prompt="Write the recap as a haiku."),
    )
    with TestClient(create_app()) as client:
        sid = client.post("/api/live", json={"title": "Sync", "language": "fr-FR"}).json()["id"]
        client.post(f"/api/live/{sid}/segments", json={"text": "We shipped the API"})

        # Nothing asked, nothing used before: the standard recap, in the
        # session's language.
        res = client.post(f"/api/live/{sid}/summary")
        assert res.status_code == 200
        assert (res.json()["template"], res.json()["language"]) == ("standard", "")
        assert prompts[-1].startswith("You are a meeting assistant.")
        assert prompts[-1].endswith("Write the entire summary in French.")

        res = client.post(f"/api/live/{sid}/summary", json={"template": "haiku", "language": "de"})
        assert res.status_code == 200
        assert (res.json()["template"], res.json()["language"]) == ("haiku", "de")
        assert prompts[-1] == "Write the recap as a haiku.\n\nWrite the entire summary in German."

        # Remembered: the next recap (e.g. the draft written when a session
        # ends) starts from them.
        catalog = client.get("/api/live/summary-templates").json()
        assert (catalog["last_template"], catalog["last_language"]) == ("haiku", "de")
        client.post(f"/api/live/{sid}/summary")
        assert prompts[-1] == "Write the recap as a haiku.\n\nWrite the entire summary in German."

        # "" writes in the session's language again.
        client.post(f"/api/live/{sid}/summary", json={"language": ""})
        assert prompts[-1].endswith("in French.")

        missing = client.post(f"/api/live/{sid}/summary", json={"template": "gone"})
        assert missing.status_code == 400
        assert "gone" in missing.json()["detail"]
        assert (
            client.post(f"/api/live/{sid}/summary", json={"language": "klingon"}).status_code == 422
        )


def test_the_transcript_recap_uses_the_template_too(
    defs_root: Path, prompts: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import precursor.backend.routers.live as live_router

    async def _fake_transcript(external_meeting: Any, transcript_ids: Any = None) -> Any:
        return True, "Alex: We ship on Friday.", None

    monkeypatch.setattr(live_router, "fetch_meeting_transcript", _fake_transcript)
    with TestClient(create_app()) as client:
        sid = client.post("/api/live", json={"title": "Teams"}).json()["id"]
        client.post(
            f"/api/live/{sid}/meeting",
            json={"subject": "Review", "is_online": True, "join_url": "https://x"},
        )
        res = client.post(
            f"/api/live/{sid}/summary/from-transcript",
            json={"transcript_ids": ["T1"], "template": "action-items", "language": "es"},
        )
        assert res.status_code == 200
        assert (res.json()["template"], res.json()["language"]) == ("action-items", "es")
        assert res.json()["transcript_ids"] == ["T1"]
        assert "follow-through" in prompts[-1]
        assert prompts[-1].endswith("Write the entire summary in Spanish.")


def test_editing_a_builtin_saves_it_as_a_definition_file(defs_root: Path) -> None:
    with TestClient(create_app()) as client:
        res = client.post("/api/live/summary-templates/files", json={"template": "standard"})
        assert res.status_code == 200
        body = res.json()
        assert (body["id"], body["path"], body["created"]) == (
            "standard",
            "summaries/standard.summary.yaml",
            True,
        )
        # In the built-in Definitions workspace, opened at the same path.
        assert body["workspace"]["slug"] == "definitions"
        assert body["workspace"]["name"] == "Definitions"
        assert body["workspace_path"] == "summaries/standard.summary.yaml"
        text = (defs_root / body["path"]).read_text(encoding="utf-8")
        assert text.startswith("# A summary template for live sessions")
        assert yaml.safe_load(text)["id"] == "standard"

        # It now declares the template; asking again opens the same file.
        listed = client.get("/api/live/summary-templates").json()["templates"]
        standard = next(t for t in listed if t["id"] == "standard")
        assert (standard["source"], standard["overrides_builtin"]) == ("file", True)
        again = client.post("/api/live/summary-templates/files", json={"template": "standard"})
        assert (again.json()["path"], again.json()["created"]) == (body["path"], False)

        # Duplicating saves a new template under a new id.
        dup = client.post(
            "/api/live/summary-templates/files", json={"template": "standard", "duplicate": True}
        ).json()
        assert (dup["id"], dup["path"], dup["created"]) == (
            "standard-copy",
            "summaries/standard-copy.summary.yaml",
            True,
        )
        copy = yaml.safe_load((defs_root / dup["path"]).read_text(encoding="utf-8"))
        assert (copy["kind"], copy["name"]) == ("summary", "Meeting recap (copy)")
        assert copy["prompt"].startswith("You are a meeting assistant.")
        listed = client.get("/api/live/summary-templates").json()
        assert "standard-copy" in [t["id"] for t in listed["templates"]]
        assert listed["problems"] == []

        # The check knows the files, and they have no database row to link to.
        report = client.get("/api/definitions/check").json()
        kinds = {f["path"]: (f["kind"], f["valid"], f["linked"]) for f in report["files"]}
        assert kinds["summaries/standard-copy.summary.yaml"] == ("summary", True, None)

        assert (
            client.post("/api/live/summary-templates/files", json={"template": "nope"}).status_code
            == 404
        )


def test_the_editor_gets_the_summary_schema() -> None:
    with TestClient(create_app()) as client:
        schema = client.get("/api/definitions/schema/summary").json()
    assert schema["title"] == "Precursor summary template"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"kind", "id", "name", "prompt"}


def test_a_duplicate_of_a_long_name_stays_valid(defs_root: Path) -> None:
    long_name = "N" * 80
    _write(
        defs_root,
        "summaries/long.summary.yaml",
        TEMPLATE.format(id="long", name=long_name, prompt="Long."),
    )
    with TestClient(create_app()) as client:
        dup = client.post(
            "/api/live/summary-templates/files", json={"template": "long", "duplicate": True}
        ).json()
        listed = client.get("/api/live/summary-templates").json()
    assert dup["id"] == "long-copy"
    assert listed["problems"] == []
    copy = next(t for t in listed["templates"] if t["id"] == "long-copy")
    assert copy["name"].endswith(" (copy)") and len(copy["name"]) == 80


def test_a_builtin_is_not_saved_under_an_id_another_file_uses(defs_root: Path) -> None:
    _write(defs_root, "agents/standard.agent.yaml", "kind: agent\nid: standard\ntitle: Std\n")
    with TestClient(create_app()) as client:
        res = client.post("/api/live/summary-templates/files", json={"template": "standard"})
        assert res.status_code == 409
        assert "agents/standard.agent.yaml" in res.json()["detail"]
        assert not (defs_root / "summaries").exists()
        # A copy under a new id is still possible.
        dup = client.post(
            "/api/live/summary-templates/files", json={"template": "standard", "duplicate": True}
        )
        assert dup.status_code == 200 and dup.json()["id"] == "standard-copy"
