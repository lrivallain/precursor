"""The definitions folder: loading, the integrity check, the CLI and the export.

Roadmap steps 2 and 3 (docs/definitions.md). The folder is read and written
here, but nothing at runtime uses it yet — so these tests pin what the check
reports and what the export writes, not how anything runs.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.schemas.definitions import AgentDefinition, WorkflowDefinition
from precursor.backend.services.definitions import cli
from precursor.backend.services.definitions.checker import InstanceNames, build_report
from precursor.backend.services.definitions.loader import load_definitions
from tests.definitions_support import mark_database, restore_database

AGENT = "kind: agent\nid: {id}\ntitle: {title}\n"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _messages(root: Path, names: InstanceNames | None = None) -> list[tuple[str, str, str]]:
    report = build_report(load_definitions(root), names)
    return [(i.severity, i.path or "", i.message) for i in report.issues]


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    _write(tmp_path, "agents/writer.agent.yaml", AGENT.format(id="writer", title="Writer"))
    _write(
        tmp_path,
        "workflows/pipe.workflow.yaml",
        "kind: workflow\nid: pipe\nname: Pipe\nsteps:\n"
        "  - key: write\n    agent: agents/writer.agent.yaml\n",
    )
    return tmp_path


# --- Loading ----------------------------------------------------------------


def test_a_consistent_folder_is_ok(folder: Path) -> None:
    report = build_report(load_definitions(folder))
    assert report.ok and report.exists
    assert [(f.path, f.kind, f.id, f.name, f.valid) for f in report.files] == [
        ("agents/writer.agent.yaml", "agent", "writer", "Writer", True),
        ("workflows/pipe.workflow.yaml", "workflow", "pipe", "Pipe", True),
    ]
    assert all(len(f.content_hash) == 64 for f in report.files)
    # Checked without a database, so link status is unknown rather than false.
    assert all(f.linked is None for f in report.files)


def test_a_missing_folder_is_reported_not_raised(tmp_path: Path) -> None:
    report = build_report(load_definitions(tmp_path / "nope"))
    assert (report.exists, report.ok, report.files) == (False, True, [])


def test_only_definition_files_outside_hidden_folders_are_read(folder: Path) -> None:
    _write(folder, ".git/objects/x.agent.yaml", "not: read")
    _write(folder, "agents/.draft.agent.yaml", "not: read")
    _write(folder, "notes/readme.yaml", "kind: agent")
    _write(folder, "agents/agent.yaml", "kind: agent")
    paths = [f.path for f in load_definitions(folder).files]
    assert paths == ["agents/writer.agent.yaml", "workflows/pipe.workflow.yaml"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "file is empty"),
        ("- a\n- b\n", "expected a mapping"),
        ("kind: agent\nid: [unclosed\n", "not valid YAML (line 3, column 1)"),
        ("kind: agent\nid: a\ntitle: A\nprompt: one\nprompt: two\n", "duplicate key 'prompt'"),
        ("kind: workflow\nid: a\nname: A\n", "needs `kind: agent`, found `kind: workflow`"),
        ("id: a\ntitle: A\n", "needs `kind: agent`, found no `kind`"),
    ],
)
def test_unreadable_files_become_errors(tmp_path: Path, text: str, message: str) -> None:
    _write(tmp_path, "agents/a.agent.yaml", text)
    [(severity, path, got)] = _messages(tmp_path)
    assert (severity, path) == ("error", "agents/a.agent.yaml")
    assert message in got


def test_non_utf8_file_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "a.agent.yaml").write_bytes(b"kind: agent\ntitle: \xff\n")
    assert _messages(tmp_path) == [("error", "a.agent.yaml", "file is not UTF-8 text")]


def test_validation_errors_are_located_by_step_key(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "w.workflow.yaml",
        "kind: workflow\nid: w\nname: W\nsteps:\n"
        "  - key: first\n    prompt: go\n"
        "  - key: second\n    prompt: go\n    contxt: {}\n",
    )
    report = build_report(load_definitions(tmp_path))
    [issue] = report.issues
    assert issue.location == "steps[second].contxt"
    assert issue.message == "Extra inputs are not permitted"


def test_edits_are_picked_up_and_unchanged_files_come_from_cache(folder: Path) -> None:
    first = load_definitions(folder).by_path["agents/writer.agent.yaml"]
    assert load_definitions(folder).by_path["agents/writer.agent.yaml"] is first

    path = folder / "agents/writer.agent.yaml"
    path.write_text(AGENT.format(id="writer", title="Renamed"), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    again = load_definitions(folder).by_path["agents/writer.agent.yaml"]
    assert again.name == "Renamed"
    assert again.content_hash != first.content_hash


def test_symlink_escaping_the_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.agent.yaml"
    outside.write_text(AGENT.format(id="o", title="O"), encoding="utf-8")
    root = tmp_path / "defs"
    root.mkdir()
    (root / "link.agent.yaml").symlink_to(outside)
    [(severity, _, message)] = _messages(root)
    assert severity == "error" and "outside the definitions folder" in message


# --- Cross-file checks ------------------------------------------------------


def test_duplicate_ids_flag_every_file_involved(folder: Path) -> None:
    _write(folder, "agents/copy.agent.yaml", AGENT.format(id="writer", title="Copy"))
    issues = [i for i in _messages(folder) if "also used by" in i[2]]
    assert [(s, p) for s, p, _ in issues] == [
        ("error", "agents/copy.agent.yaml"),
        ("error", "agents/writer.agent.yaml"),
    ]


def test_an_invalid_file_still_counts_for_duplicate_ids(folder: Path) -> None:
    _write(folder, "agents/broken.agent.yaml", "kind: agent\nid: writer\n")
    messages = [m for _, p, m in _messages(folder) if p == "agents/writer.agent.yaml"]
    assert any("also used by agents/broken.agent.yaml" in m for m in messages)


def test_a_dangling_agent_path_suggests_the_file_it_probably_meant(folder: Path) -> None:
    shutil.move(folder / "agents", folder / "team")
    [(severity, path, message)] = _messages(folder)
    assert (severity, path) == ("error", "workflows/pipe.workflow.yaml")
    assert "no agent file at agents/writer.agent.yaml" in message
    assert "did you mean team/writer.agent.yaml?" in message


def test_a_step_pointing_at_a_broken_agent_cannot_run(folder: Path) -> None:
    _write(folder, "agents/writer.agent.yaml", "kind: agent\nid: writer\n")
    messages = _messages(folder)
    assert (
        "error",
        "workflows/pipe.workflow.yaml",
        "agents/writer.agent.yaml has errors, so this step can't run",
    ) in messages


def test_unknown_roles_and_mcp_servers_are_warnings(folder: Path) -> None:
    _write(
        folder,
        "agents/writer.agent.yaml",
        AGENT.format(id="writer", title="Writer")
        + "role: analyst\ncapabilities:\n  mcp_servers: [fetch, ghost]\n",
    )
    _write(
        folder,
        "workflows/pipe.workflow.yaml",
        "kind: workflow\nid: pipe\nname: Pipe\nrole: Nobody\nsteps:\n"
        "  - key: write\n    agent: agents/writer.agent.yaml\n"
        "    capabilities:\n      mcp_servers: [phantom]\n",
    )
    names = InstanceNames(roles=frozenset({"Analyst"}), mcp_servers=frozenset({"fetch"}))
    report = build_report(load_definitions(folder), names)
    assert report.ok  # warnings never fail the check
    assert [(i.severity, i.location) for i in report.issues] == [
        ("warning", "capabilities.mcp_servers"),
        ("warning", "role"),
        ("warning", "steps[write].capabilities.mcp_servers"),
    ]
    joined = " ".join(i.message for i in report.issues)
    # Role names match case-insensitively, like the transfer import does.
    assert "'analyst'" not in joined and "'Nobody'" in joined
    assert "'ghost'" in joined and "'fetch'" not in joined and "'phantom'" in joined


def test_name_checks_are_skipped_without_an_instance(folder: Path) -> None:
    _write(folder, "agents/writer.agent.yaml", AGENT.format(id="writer", title="W") + "role: X\n")
    assert _messages(folder) == []


# --- CLI --------------------------------------------------------------------


def test_cli_passes_a_clean_folder(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(folder)]) == 0
    assert "Checked 2 files" in capsys.readouterr().out


def test_cli_fails_on_errors(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (folder / "agents/writer.agent.yaml").unlink()
    assert cli.main([str(folder)]) == 1
    out = capsys.readouterr().out
    assert "error: workflows/pipe.workflow.yaml: steps[write].agent: no agent file" in out
    assert "1 error, 0 warnings" in out


def test_cli_json_and_missing_folder(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(folder), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main([str(folder / "nope")]) == 2
    assert "is not a folder" in capsys.readouterr().err


def test_cli_checks_the_docs_examples() -> None:
    examples = Path(__file__).resolve().parents[1] / "docs" / "examples" / "definitions"
    assert cli.main([str(examples)]) == 0


# --- API: check & export ----------------------------------------------------


def _root() -> Path:
    from precursor.backend.config import get_settings

    return Path(get_settings().definitions_dir)


# Workflows a test created, dropped at teardown. The database is shared by the
# whole suite, and some later tests delete every agent without touching steps:
# agent ids then restart, and a leftover step would silently point at whatever
# agent reuses its old id.
_created_workflows: list[int] = []


@pytest.fixture
async def api_root() -> AsyncIterator[Path]:
    mark = await mark_database()
    root = _root()
    shutil.rmtree(root, ignore_errors=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)
    await restore_database(mark)
    _created_workflows.clear()


async def _setup(enabled: bool = True) -> None:
    from precursor.backend.db import SessionLocal, init_db
    from precursor.backend.models import AppSetting

    await init_db()
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "agents_enabled")
        if row is None:
            session.add(AppSetting(key="agents_enabled", value=json.dumps(enabled)))
        else:
            row.value = json.dumps(enabled)
        await session.commit()


def _uid() -> str:
    # Starts with a letter: an all-digit id written unquoted into YAML (as some
    # tests do) would parse as a number and fail validation.
    return "t" + uuid.uuid4().hex[:8]


async def _seed() -> tuple[dict[str, int], str]:
    """A reusable agent, an ad-hoc one, and a workflow exercising every step shape."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Chat, Role, Workflow, WorkflowStep

    tag = _uid()
    async with SessionLocal() as session:
        role = Role(name=f"Analyst {tag}", system_prompt="be sharp")
        chat = Chat(title="c", slug=f"defs-chat-{tag}")
        session.add_all([role, chat])
        await session.flush()
        writer = AgentSession(
            title=f"Writer {tag}",
            task_prompt="Write it.\nKeep it short.",
            status="waiting",
            model="gpt-5",
            role_id=role.id,
            approval_policy="balanced",
            autonomy_enabled=True,
            max_steps=5,
            use_skills=False,
            mcp_servers="fetch, workiq",
            token_budget=1000,
            max_retries=2,
        )
        adhoc = AgentSession(
            title=f"Quick look {tag}", task_prompt="peek", status="waiting", chat_id=chat.id
        )
        vessel = AgentSession(
            title="Checker",
            task_prompt="PASS if fine",
            status="waiting",
            inline=True,
            model="gpt-5-mini",
            use_memory=False,
        )
        session.add_all([writer, adhoc, vessel])
        await session.flush()
        wf = Workflow(
            name=f"Pipeline {tag}",
            status="idle",
            description="does things",
            role_id=role.id,
            approval_policy="autonomous",
            max_loops=5,
            step_timeout_seconds=600,
            clear_artifacts=False,
        )
        session.add(wf)
        await session.flush()
        session.add_all(
            [
                WorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    agent_id=writer.id,
                    name="Draft",
                    on_error="retry",
                    max_retries=3,
                    mcp_servers="",
                ),
                WorkflowStep(
                    workflow_id=wf.id,
                    position=1,
                    agent_id=vessel.id,
                    kind="gate",
                    on_fail_position=0,
                    context_mode="selected",
                    context_sources="0,2",
                    instructions="  look closely  ",
                ),
                WorkflowStep(
                    workflow_id=wf.id,
                    position=2,
                    kind="approval",
                    name="Sign off",
                    on_fail_position=0,
                    on_reject="stop",
                ),
            ]
        )
        await session.commit()
        _created_workflows.append(wf.id)
        ids = {"writer": writer.id, "adhoc": adhoc.id, "vessel": vessel.id, "workflow": wf.id}
        return ids, tag


def _written(result: dict, kind: str, source_id: int) -> dict:
    [entry] = [e for e in result["written"] if e["kind"] == kind and e["source_id"] == source_id]
    return entry


def _load(root: Path, rel: str) -> dict:
    return yaml.safe_load((root / rel).read_text(encoding="utf-8"))


async def test_export_writes_valid_linked_files(api_root: Path) -> None:
    await _setup()
    ids, tag = await _seed()

    with TestClient(create_app()) as client:
        resp = client.post("/api/definitions/export")
        assert resp.status_code == 200, resp.text
        result = resp.json()
        check = client.get("/api/definitions/check").json()

    writer = _written(result, "agent", ids["writer"])
    adhoc = _written(result, "agent", ids["adhoc"])
    wf_entry = _written(result, "workflow", ids["workflow"])
    assert writer["path"] == f"agents/writer-{tag}.agent.yaml"
    assert adhoc["path"] == f"agents/adhoc/quick-look-{tag}.agent.yaml"
    assert wf_entry["path"] == f"workflows/pipeline-{tag}.workflow.yaml"
    # Step-private agents get no file of their own.
    assert all(e["source_id"] != ids["vessel"] for e in result["written"] if e["kind"] == "agent")

    agent_doc = _load(api_root, writer["path"])
    AgentDefinition.model_validate(agent_doc)
    assert agent_doc == {
        "kind": "agent",
        "id": writer["id"],
        "title": f"Writer {tag}",
        "prompt": "Write it.\nKeep it short.",
        "model": "gpt-5",
        "role": f"Analyst {tag}",
        "approval_policy": "balanced",
        "autonomy": {"enabled": True, "max_steps": 5},
        "capabilities": {"skills": False, "mcp_servers": ["fetch", "workiq"]},
        "limits": {"token_budget": 1000, "max_retries": 2},
    }
    raw = (api_root / writer["path"]).read_text(encoding="utf-8")
    assert raw.startswith("# Keep `id` unchanged")
    assert "prompt: |" in raw  # multi-line prompts stay readable

    wf_doc = _load(api_root, wf_entry["path"])
    WorkflowDefinition.model_validate(wf_doc)
    assert {k: v for k, v in wf_doc.items() if k != "steps"} == {
        "kind": "workflow",
        "id": wf_entry["id"],
        "name": f"Pipeline {tag}",
        "description": "does things",
        "role": f"Analyst {tag}",
        "approval_policy": "autonomous",
        "clear_artifacts": False,
        "max_loops": 5,
        "step_timeout_seconds": 600,
    }
    assert wf_doc["steps"] == [
        {
            "key": "draft",
            "name": "Draft",
            "agent": writer["path"],
            "on_error": "retry",
            "max_retries": 3,
            "capabilities": {"mcp_servers": []},
        },
        {
            "key": "checker",
            "kind": "gate",
            "prompt": "PASS if fine",
            "model": "gpt-5-mini",
            "on_fail": "draft",
            "instructions": "look closely",
            "context": {"mode": "selected", "from": ["draft"]},
            # The hidden agent had memory off; with no agent file to inherit
            # from, the step now has to say so itself.
            "capabilities": {"memory": False},
        },
        {
            "key": "sign-off",
            "name": "Sign off",
            "kind": "approval",
            "on_fail": "draft",
            "on_reject": "stop",
        },
    ]
    # Source 2 (the approval step itself, later than the gate) can't be a context
    # source in the file format, so it's reported rather than silently dropped.
    assert any(
        i["path"] == wf_entry["path"] and "not earlier steps" in i["message"]
        for i in result["issues"]
    )

    by_path = {f["path"]: f for f in check["files"]}
    assert by_path[writer["path"]]["linked"] is True
    assert by_path[wf_entry["path"]]["linked"] is True
    unlinked = check["database"]
    assert ids["writer"] not in {a["id"] for a in unlinked["unlinked_agents"]}
    assert ids["workflow"] not in {w["id"] for w in unlinked["unlinked_workflows"]}
    # Other tests share this database, so only this test's files are asserted clean.
    mine = {writer["path"], adhoc["path"], wf_entry["path"]}
    assert not [i for i in check["issues"] if i["severity"] == "error" and i["path"] in mine]


async def test_export_is_idempotent_and_overwrite_rewrites_in_place(api_root: Path) -> None:
    await _setup()
    ids, _ = await _seed()

    with TestClient(create_app()) as client:
        first = client.post("/api/definitions/export").json()
        path = _written(first, "agent", ids["writer"])["path"]
        original = (api_root / path).read_text(encoding="utf-8")

        # A hand edit survives a plain re-export: the row is already linked.
        (api_root / path).write_text(original + "# my note\n", encoding="utf-8")
        second = client.post("/api/definitions/export").json()
        assert second["written"] == []
        assert any(e["source_id"] == ids["writer"] for e in second["skipped"])
        assert (api_root / path).read_text(encoding="utf-8").endswith("# my note\n")

        # overwrite regenerates the same file from the database.
        third = client.post("/api/definitions/export", params={"overwrite": True}).json()
        assert _written(third, "agent", ids["writer"])["path"] == path
        assert (api_root / path).read_text(encoding="utf-8") == original


async def test_export_keeps_an_existing_portable_id(api_root: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    await _setup()
    ids, _ = await _seed()
    portable = str(uuid.uuid4())
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, ids["adhoc"])
        assert agent is not None
        agent.export_id = portable
        await session.commit()

    with TestClient(create_app()) as client:
        result = client.post("/api/definitions/export").json()
    assert _written(result, "agent", ids["adhoc"])["id"] == portable


async def test_export_never_reuses_a_summary_templates_id(api_root: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    await _setup()
    ids, _ = await _seed()
    taken = _uid()
    _write(
        api_root, "summaries/t.summary.yaml", f"kind: summary\nid: {taken}\nname: T\nprompt: t\n"
    )
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, ids["adhoc"])
        assert agent is not None
        agent.export_id = taken
        await session.commit()

    with TestClient(create_app()) as client:
        result = client.post("/api/definitions/export").json()
        report = client.get("/api/definitions/check").json()
    assert _written(result, "agent", ids["adhoc"])["id"] != taken
    assert any("used by a summary template" in i["message"] for i in result["issues"])
    assert not [i for i in report["issues"] if "is also used by" in i["message"]]


async def test_a_step_with_a_deleted_agent_is_written_and_flagged(api_root: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow, WorkflowStep

    await _setup()
    async with SessionLocal() as session:
        wf = Workflow(name=f"Broken {_uid()}", status="idle")
        session.add(wf)
        await session.flush()
        session.add(WorkflowStep(workflow_id=wf.id, position=0, agent_id=None, name="Gone"))
        await session.commit()
        wf_id = wf.id
        _created_workflows.append(wf_id)

    with TestClient(create_app()) as client:
        result = client.post("/api/definitions/export").json()
        check = client.get("/api/definitions/check").json()

    path = _written(result, "workflow", wf_id)["path"]
    assert (api_root / path).is_file()
    assert any(
        i["severity"] == "error" and i["path"] == path and "fix it by hand" in i["message"]
        for i in result["issues"]
    )
    assert not check["ok"]
    assert any(i["path"] == path and i["severity"] == "error" for i in check["issues"])


async def test_hidden_agent_settings_are_only_flagged_when_they_would_stop_applying(
    api_root: Path,
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Role, Workflow, WorkflowStep

    await _setup()
    tag = _uid()
    async with SessionLocal() as session:
        role = Role(name=f"Persona {tag}", system_prompt="")
        session.add(role)
        await session.flush()
        wf_ids = []
        for overridden in (True, False):
            vessel = AgentSession(
                title="v",
                task_prompt="do",
                status="waiting",
                inline=True,
                role_id=role.id,
                approval_policy="autonomous",
                mcp_servers="fetch",
            )
            wf = Workflow(
                name=f"Vessel {tag} {overridden}",
                status="idle",
                role_id=role.id if overridden else None,
                approval_policy="autonomous" if overridden else None,
            )
            session.add_all([vessel, wf])
            await session.flush()
            session.add(WorkflowStep(workflow_id=wf.id, position=0, agent_id=vessel.id))
            wf_ids.append(wf.id)
            _created_workflows.append(wf.id)
        await session.commit()

    with TestClient(create_app()) as client:
        result = client.post("/api/definitions/export").json()

    covered, exposed = (_written(result, "workflow", i)["path"] for i in wf_ids)
    lost = {i["path"]: i["message"] for i in result["issues"] if "can't hold" in i["message"]}
    # The workflow's role and policy win for every step, so nothing is lost there.
    assert covered not in lost
    assert "role, approval policy" in lost[exposed]
    # The engine gives a workflow step its own server scope (null = all), never
    # the agent's, so the hidden agent's list isn't carried over.
    step = _load(api_root, covered)["steps"][0]
    assert "capabilities" not in step


async def test_linking_a_row_does_not_touch_its_updated_at(api_root: Path) -> None:
    # The Workflows gallery sorts by updated_at: an export must not reshuffle it.
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Workflow

    await _setup()
    ids, _ = await _seed()
    async with SessionLocal() as session:
        before = {
            "agent": (await session.get(AgentSession, ids["writer"])).updated_at,  # type: ignore[union-attr]
            "workflow": (await session.get(Workflow, ids["workflow"])).updated_at,  # type: ignore[union-attr]
        }

    with TestClient(create_app()) as client:
        assert client.post("/api/definitions/export").status_code == 200

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, ids["writer"])
        workflow = await session.get(Workflow, ids["workflow"])
        assert agent is not None and workflow is not None
        assert agent.export_id and workflow.export_id
        assert agent.updated_at == before["agent"]
        assert workflow.updated_at == before["workflow"]


async def test_check_reports_rows_without_a_file(api_root: Path) -> None:
    await _setup()
    ids, _ = await _seed()
    with TestClient(create_app()) as client:
        report = client.get("/api/definitions/check").json()
    # Startup made the built-in workspace's (empty) folder.
    assert report["exists"] is True and report["files"] == []
    unlinked = {a["id"] for a in report["database"]["unlinked_agents"]}
    assert {ids["writer"], ids["adhoc"]} <= unlinked
    assert ids["vessel"] not in unlinked
    assert any("have no definition file yet" in i["message"] for i in report["issues"])


async def test_check_flags_unknown_roles_against_the_database(api_root: Path) -> None:
    await _setup()
    _write(api_root, "agents/a.agent.yaml", AGENT.format(id=_uid(), title="A") + "role: Ghost\n")
    with TestClient(create_app()) as client:
        report = client.get("/api/definitions/check").json()
    assert any(
        i["severity"] == "warning" and i["location"] == "role" and "'Ghost'" in i["message"]
        for i in report["issues"]
    )


async def test_export_needs_agents_mode(api_root: Path) -> None:
    await _setup(enabled=False)
    try:
        with TestClient(create_app()) as client:
            assert client.post("/api/definitions/export").status_code == 409
            assert client.get("/api/definitions/check").status_code == 200
    finally:
        await _setup(enabled=True)


# --- Step references by key -------------------------------------------------


def test_step_outputs_resolve_by_key_only_when_keys_are_known() -> None:
    from precursor.backend.services.workflow_state import (
        UNSET_PLACEHOLDER,
        has_placeholders,
        render_placeholders,
    )

    text = "Use {{step.draft.output}} and {{step.1.output}}; {{step.gone.output | none}}"
    outputs = {0: "the draft", 1: "the review"}
    keyed = render_placeholders(text, step_outputs=outputs, step_keys={"draft": 0, "check": 1})
    assert keyed == "Use the draft and the review; none"
    # Without keys (database mode) the key form is left exactly as written.
    plain = render_placeholders(text, step_outputs=outputs)
    assert plain == "Use {{step.draft.output}} and the review; {{step.gone.output | none}}"
    assert render_placeholders("{{step.x.output}}", step_keys={"y": 0}) == UNSET_PLACEHOLDER
    assert has_placeholders("see {{step.draft.output}}")


def test_numeric_step_references_are_rewritten_by_key() -> None:
    from precursor.backend.services.workflow_state import keyed_step_references

    keys = ["draft", "check"]
    assert keyed_step_references("{{step.0.output}} / {{ step.1.output | n/a }}", keys) == (
        "{{step.draft.output}} / {{step.check.output | n/a }}"
    )
    # Out of range, or not a step reference: left alone.
    assert keyed_step_references("{{step.7.output}} {{run.input}}", keys) == (
        "{{step.7.output}} {{run.input}}"
    )


def test_check_warns_about_a_reference_to_a_missing_step(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "w.workflow.yaml",
        "kind: workflow\nid: w\nname: W\nsteps:\n"
        "  - key: a\n    prompt: go\n"
        "  - key: b\n    prompt: go\n    instructions: 'Use {{step.a.output}} and {{step.zz.output}}'\n",
    )
    report = build_report(load_definitions(tmp_path))
    assert report.ok
    [issue] = report.issues
    assert (issue.severity, issue.location) == ("warning", "steps[b].instructions")
    assert "{{step.zz.output}}" in issue.message


async def test_export_writes_step_references_by_key(api_root: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Workflow, WorkflowStep

    await _setup()
    async with SessionLocal() as session:
        vessel_a = AgentSession(title="A", task_prompt="draft it", status="waiting", inline=True)
        vessel_b = AgentSession(title="B", task_prompt="check it", status="waiting", inline=True)
        session.add_all([vessel_a, vessel_b])
        await session.flush()
        wf = Workflow(name=f"Refs {_uid()}", status="idle")
        session.add(wf)
        await session.flush()
        session.add_all(
            [
                WorkflowStep(workflow_id=wf.id, position=0, agent_id=vessel_a.id, name="Draft"),
                WorkflowStep(
                    workflow_id=wf.id,
                    position=1,
                    agent_id=vessel_b.id,
                    name="Check",
                    instructions="Review {{step.0.output}}",
                ),
            ]
        )
        await session.commit()
        _created_workflows.append(wf.id)
        wf_id = wf.id
    with TestClient(create_app()) as client:
        result = client.post("/api/definitions/export").json()
    path = _written(result, "workflow", wf_id)["path"]
    assert _load(api_root, path)["steps"][1]["instructions"] == "Review {{step.draft.output}}"
