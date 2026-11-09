"""Branch-by-branch characterisation of the automatic plan review (``_review``).

The end-to-end tests in ``test_step_plans.py`` drive the happy paths through a
real database; this file scripts the model and the plan store instead, so every
way the review can stop is pinned: what it returns, what it tells the plan's
discussion, and what it asks of the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from precursor.backend.models.step_plan import AUTO_PAUSED, STEP_TO_VALIDATE
from precursor.backend.services import step_plan_llm as llm
from precursor.backend.services import step_plan_runner as runner
from precursor.backend.services import step_plans as svc
from precursor.backend.services.step_plan_repair_prompts import NO_QUESTIONS

GOOD = SimpleNamespace(scope="final", gaps=[], contradictions=[], score=90)
GAPPY = SimpleNamespace(scope="final", gaps=["missing table"], contradictions=[], score=90)
LOW = SimpleNamespace(scope="final", gaps=[], contradictions=[], score=30)


@dataclass
class World:
    """The plan, the model's scripted answers, and everything the review did."""

    topic_id: int | None = 1
    chat_id: int | None = None
    final_output: str = "Final"
    final_stale: bool = False
    final_provisional: bool = False
    assessed_sig: str | None = "sig"
    verdict: Any = field(default_factory=lambda: GOOD)
    # Each model call pops the next scripted value.
    finals: list[str] = field(default_factory=list)
    file_problems: list[list[str]] = field(default_factory=list)
    assessments: list[Any] = field(default_factory=list)
    running: list[bool] = field(default_factory=list)
    step_review: bool = True
    step_status: str = STEP_TO_VALIDATE
    final_error: Exception | None = None
    ready: bool = True
    notes: list[str] = field(default_factory=list)
    final_calls: list[dict[str, Any]] = field(default_factory=list)
    handed_back: list[tuple[Any, ...]] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    sent: bool = False

    def plan(self) -> Any:
        return SimpleNamespace(
            source_topic_id=self.topic_id,
            source_chat_id=self.chat_id,
            final_output=self.final_output,
            final_stale=self.final_stale,
            final_provisional=self.final_provisional,
            assessed_sig=self.assessed_sig,
            assessment_json="verdict" if self.verdict is not None else None,
            final_version=3,
            title="Plan",
        )


class _Session:
    async def refresh(self, _obj: Any) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()

    async def get_plan(_s: Any, _id: int) -> Any:
        return w.plan()

    async def plan_steps(_s: Any, _id: int, refresh: bool = False) -> list[Any]:
        return [SimpleNamespace(status=w.step_status, artifact="draft")]

    async def generate_final(_s: Any, _id: int, **kwargs: Any) -> Any:
        w.final_calls.append(kwargs)
        if w.final_error is not None:
            raise w.final_error
        outcome = w.finals.pop(0)
        if outcome == "published":
            w.final_output, w.final_stale, w.final_provisional = "Rebuilt", False, False
        return SimpleNamespace(outcome=outcome)

    async def final_file_problems(_s: Any, _id: int) -> tuple[list[str], list[str]]:
        return (w.file_problems.pop(0) if w.file_problems else []), ["out/report.md"]

    async def assess_plan(_s: Any, _id: int) -> None:
        w.verdict = w.assessments.pop(0)
        w.assessed_sig = "sig"

    async def send_to_source(_s: Any, _id: int, _text: Any, *, assessed: Any) -> Any:
        w.sent = True
        assert (assessed.signature, assessed.version) == ("sig", 3)
        return w.plan(), SimpleNamespace(id=99)

    async def note(_id: int, text: str) -> None:
        w.notes.append(text)

    async def still_running(_id: int) -> bool:
        return w.running.pop(0) if w.running else True

    async def set_state(_id: int, state: str) -> None:
        w.states.append(state)

    async def review_step(*_args: Any) -> bool:
        return w.step_review

    async def published(_id: int) -> None:
        return None

    def hand_back(*args: Any, **kwargs: Any) -> None:
        w.handed_back.append((args, kwargs))

    monkeypatch.setattr(runner, "SessionLocal", _Session)
    monkeypatch.setattr(svc, "get_plan", get_plan)
    monkeypatch.setattr(svc, "plan_steps", plan_steps)
    monkeypatch.setattr(svc, "working_leaves", lambda steps: steps)
    monkeypatch.setattr(svc, "parse_assessment", lambda raw: w.verdict if raw else None)
    monkeypatch.setattr(svc, "is_ready", lambda _steps: w.ready)
    monkeypatch.setattr(svc, "plan_signature", lambda _plan, _steps: "sig")
    monkeypatch.setattr(llm, "generate_final", generate_final)
    monkeypatch.setattr(llm, "final_file_problems", final_file_problems)
    monkeypatch.setattr(llm, "assess_plan", assess_plan)
    monkeypatch.setattr(llm, "send_to_source", send_to_source)
    monkeypatch.setattr(runner, "_note", note)
    monkeypatch.setattr(runner, "_still_running", still_running)
    monkeypatch.setattr(runner, "_set_state", set_state)
    monkeypatch.setattr(runner, "_review_step", review_step)
    monkeypatch.setattr(runner, "publish_step_plan_changed", published)
    monkeypatch.setattr(runner, "hand_back", hand_back)
    monkeypatch.setattr(runner, "_RETRY_DELAY", 0)
    return w


async def _review(
    world: World,
    policy: str = "balanced",
    *,
    generated_final: bool = False,
    require_running: bool = False,
) -> bool:
    return await runner._review(
        1, policy, generated_final=generated_final, require_running=require_running
    )


# (World overrides, review kwargs, expected result, text the last note contains)
SCENARIOS: dict[str, tuple[dict[str, Any], dict[str, Any], bool, str | None]] = {
    "no source": ({"topic_id": None}, {}, False, None),
    "stopped before starting": ({"running": [False]}, {"require_running": True}, False, None),
    "stopped between steps": ({"running": [True, False]}, {"require_running": True}, False, None),
    "a step stays weak": ({"step_review": False}, {}, False, None),
    "validated steps are left alone": (
        {"step_review": False, "step_status": "validated"},
        {},
        True,
        "Drafts validated.",
    ),
    "existing final is stale": (
        {"final_stale": True},
        {},
        False,
        "existing final output needs review and was preserved",
    ),
    "final written after a discussion": (
        {"final_output": "", "finals": ["discussion", "published"]},
        {},
        True,
        "Drafts validated. The final output is ready",
    ),
    "stopped after drafting the final": (
        {"final_output": "", "finals": ["published"], "running": [True, True, False]},
        {"require_running": True},
        False,
        None,
    ),
    "stopped after the discussion retry": (
        {
            "final_output": "",
            "finals": ["discussion", "published"],
            "running": [True] * 3 + [False],
        },
        {"require_running": True},
        False,
        None,
    ),
    "final never written": (
        {"final_provisional": True, "finals": ["awaiting_approval"]},
        {"generated_final": True},
        False,
        "did not write the final output",
    ),
    "existing final has a file problem": (
        {"file_problems": [["bad path"]]},
        {},
        False,
        "existing final output has a file problem (bad path)",
    ),
    "stopped before the file fix": (
        {"file_problems": [["bad path"]], "running": [True, True, False]},
        {"generated_final": True, "require_running": True},
        False,
        None,
    ),
    "stopped after the file fix": (
        {"file_problems": [["bad path"]], "finals": ["published"], "running": [True] * 3 + [False]},
        {"generated_final": True, "require_running": True},
        False,
        "Rebuilding it once.",
    ),
    "file problem persists": (
        {"file_problems": [["bad path"], ["still bad"]], "finals": ["published"]},
        {"generated_final": True},
        False,
        "still has a file problem. It was not filed in the topic",
    ),
    "file problem fixed": (
        {"file_problems": [["bad path"], []], "finals": ["published"]},
        {"generated_final": True},
        True,
        "Drafts validated.",
    ),
    "file fix not written": (
        {"file_problems": [["bad path"], []], "finals": ["discussion"]},
        {"generated_final": True},
        False,
        "did not write the final output. Regenerate it from the plan, then send it to the topic",
    ),
    "plan changed during review": (
        {"ready": False},
        {},
        False,
        "changed during review",
    ),
    "stopped before the verdict": (
        {"running": [True, True, False]},
        {"require_running": True},
        False,
        None,
    ),
    "assessment still missing": (
        {"verdict": None, "assessments": [None]},
        {},
        False,
        "has no current assessment. It was not filed",
    ),
    "stopped while assessing": (
        {"verdict": None, "assessments": [GOOD], "running": [True, True, True, False]},
        {"require_running": True},
        False,
        None,
    ),
    "assessed on the way": ({"verdict": None, "assessments": [GOOD]}, {}, True, "Drafts"),
    "stale signature reassessed": ({"assessed_sig": "old", "assessments": [GOOD]}, {}, True, None),
    "gaps in a final it did not write": (
        {"verdict": GAPPY},
        {},
        False,
        "still has unresolved gaps or contradictions (missing table)",
    ),
    "gap repair needs an approval": (
        {"verdict": GAPPY, "finals": ["awaiting_approval"]},
        {"generated_final": True},
        False,
        "Paused: a tool call",
    ),
    "gap repair not written": (
        {"verdict": GAPPY, "finals": ["discussion"]},
        {"generated_final": True},
        False,
        "could not be rebuilt",
    ),
    "gap repair breaks a file": (
        {"verdict": GAPPY, "finals": ["published"], "file_problems": [[], ["bad"]]},
        {"generated_final": True},
        False,
        "rebuilt final output has a file problem (bad)",
    ),
    "gaps remain after the repair": (
        {"verdict": GAPPY, "finals": ["published"]},
        {"generated_final": True},
        False,
        "still has unresolved gaps",
    ),
    "stopped before the gap repair": (
        {"verdict": GAPPY, "running": [True, True, True, False]},
        {"generated_final": True, "require_running": True},
        False,
        "identified gaps or contradictions",
    ),
    "stopped after the gap repair": (
        {"verdict": GAPPY, "finals": ["published"], "running": [True] * 4 + [False]},
        {"generated_final": True, "require_running": True},
        False,
        "identified gaps or contradictions",
    ),
    "low score": ({"verdict": LOW}, {}, False, "scored 30/100 against the ask (no gaps listed)"),
    "model keeps failing": (
        {"final_output": "", "final_error": RuntimeError("boom")},
        {},
        False,
        "Automatic review stopped, please review the plan yourself: boom",
    ),
    "model fails after the run stopped": (
        {"final_output": "", "final_error": RuntimeError("boom"), "running": [True, True, False]},
        {"require_running": True},
        False,
        None,
    ),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_review_outcome(world: World, name: str) -> None:
    overrides, kwargs, expected, last_note = SCENARIOS[name]
    for key, value in overrides.items():
        setattr(world, key, value)
    assert await _review(world, **kwargs) is expected
    if last_note is None:
        assert not any("Automatic review stopped" in n for n in world.notes)
    else:
        assert world.notes and last_note in world.notes[-1], world.notes
    assert not world.sent
    assert not world.handed_back


async def test_discussion_retry_and_file_fix_instructions(world: World) -> None:
    world.final_output = ""
    world.finals = ["discussion", "published", "published"]
    world.file_problems = [["bad path"], []]
    assert await _review(world) is True
    assert world.final_calls[0] == {"force": True, "automatic": True}
    assert world.final_calls[1] == {
        "force": True,
        "instructions": NO_QUESTIONS,
        "automatic": True,
    }
    fix = world.final_calls[2]["instructions"]
    assert fix.startswith(NO_QUESTIONS)
    assert "bad path" in fix and "out/report.md" in fix


async def test_gap_repair_rebuilds_once_then_files(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.verdict = GAPPY
    world.finals = ["published"]
    scripted = llm.generate_final

    async def generate_then_reassess(session: Any, plan_id: int, **kwargs: Any) -> Any:
        result = await scripted(session, plan_id, **kwargs)
        world.verdict, world.assessed_sig, world.assessments = None, None, [GOOD]
        return result

    monkeypatch.setattr(llm, "generate_final", generate_then_reassess)
    assert await _review(world, generated_final=True) is True
    assert "missing table" in world.final_calls[0]["instructions"]
    assert world.notes[-1].startswith("Drafts validated.")


async def test_gap_repair_waiting_on_an_approval_pauses_the_run(world: World) -> None:
    world.verdict = GAPPY
    world.finals = ["awaiting_approval"]
    assert await _review(world, generated_final=True) is False
    assert world.states == [AUTO_PAUSED]


@pytest.mark.parametrize(
    ("topic_id", "chat_id", "kind"),
    [(1, None, {}), (None, 7, {"source_kind": "chat"})],
)
async def test_autonomous_files_the_final_and_hands_back(
    world: World, topic_id: int | None, chat_id: int | None, kind: dict[str, str]
) -> None:
    world.topic_id, world.chat_id = topic_id, chat_id
    assert await _review(world, "autonomous") is True
    assert world.sent
    source = "topic" if topic_id is not None else "chat"
    assert world.notes[-1] == (
        f"Drafts validated and the final output filed in the {source}, which now reviews it."
    )
    assert world.handed_back == [
        (
            (topic_id or chat_id, "Plan"),
            {
                "reviewed_by_user": False,
                **kind,
                "plan_id": 1,
                "posted_message_id": 99,
                "final_version": 3,
            },
        )
    ]


async def test_autonomous_stops_before_filing_when_the_run_stopped(world: World) -> None:
    world.running = [True, True, True, False]
    assert await _review(world, "autonomous", require_running=True) is False
    assert not world.sent
