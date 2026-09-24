"""Seed a throwaway Precursor database with demo data for documentation screenshots.

The website's screenshots must never show real data or a real GitHub account
(see ``website/features/AGENTS.md``). This script builds a self-contained demo
database from scratch so every shot is reproducible and privacy-safe:

    PRECURSOR_DATABASE_URL=sqlite+aiosqlite:///./demo.db \\
    PRECURSOR_DATA_DIR=./demo-data \\
    uv run python scripts/seed_demo.py

It writes **only** to the database and data directory named by those variables,
and refuses to touch the default ``precursor.db`` so a stray invocation can't
overwrite a real install. Run the demo server with ``gh`` off its ``PATH`` and no
GitHub token saved, so the persona footer reads "Guest / Not connected".
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _guard() -> None:
    """Refuse to run against anything that looks like a real install."""
    url = os.environ.get("PRECURSOR_DATABASE_URL", "")
    if not url:
        raise SystemExit("Set PRECURSOR_DATABASE_URL to a throwaway database first.")
    if "demo" not in url:
        raise SystemExit(f"Refusing to seed a database whose URL has no 'demo' in it: {url}")
    if not os.environ.get("PRECURSOR_DATA_DIR", ""):
        raise SystemExit("Set PRECURSOR_DATA_DIR to a throwaway directory first.")


_guard()

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from precursor.backend.db import SessionLocal, init_db  # noqa: E402
from precursor.backend.models.agent_artifact import AgentArtifact  # noqa: E402
from precursor.backend.models.agent_event import AgentEventRecord  # noqa: E402
from precursor.backend.models.agent_run import AgentRun  # noqa: E402
from precursor.backend.models.agent_session import AgentSession  # noqa: E402
from precursor.backend.models.chat import Chat  # noqa: E402
from precursor.backend.models.collection import Collection  # noqa: E402
from precursor.backend.models.meeting import (  # noqa: E402
    MeetingInsight,
    MeetingSegment,
    MeetingSession,
)
from precursor.backend.models.memory import Memory  # noqa: E402
from precursor.backend.models.message import Message, MessageRole  # noqa: E402
from precursor.backend.models.role import Role  # noqa: E402
from precursor.backend.models.settings import AppSetting  # noqa: E402
from precursor.backend.models.skill import Skill  # noqa: E402
from precursor.backend.models.topic import Topic  # noqa: E402
from precursor.backend.models.topic_schedule import TopicSchedule  # noqa: E402
from precursor.backend.models.topic_summary import TopicSummary  # noqa: E402
from precursor.backend.models.workflow import (  # noqa: E402
    Workflow,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowStep,
)
from precursor.backend.models.workflow_state import WorkflowState  # noqa: E402
from precursor.backend.models.workspace import Workspace  # noqa: E402
from precursor.backend.schemas.agent import AgentEvent  # noqa: E402
from precursor.backend.services.agents.directives import (  # noqa: E402
    _CONTINUE_NUDGE,
    strip_control_directives,
)
from precursor.backend.services.schedule_timing import RecurrenceRule  # noqa: E402

NOW = datetime.now(UTC)


def ago(**kw: float) -> datetime:
    return NOW - timedelta(**kw)


# --------------------------------------------------------------------------
# Skills live on disk as <skills_dir>/<name>/SKILL.md, shared with the Copilot
# CLI. Point PRECURSOR_SKILLS_DIR at a demo folder and write fixtures into it.
# --------------------------------------------------------------------------
DEMO_SKILLS = {
    "release-notes": (
        "Turn a list of merged pull requests into release notes",
        "Group the changes by theme, lead each entry with the user-visible\n"
        "effect rather than the implementation, and keep every bullet to one\n"
        "sentence. Drop pure refactors and dependency bumps unless they change\n"
        "behaviour.",
    ),
    "rewrite": (
        "Rewrite text to be clearer and more concise",
        "Preserve the author's meaning and register. Cut hedging and filler,\n"
        "prefer the active voice, and never add a claim that wasn't there.",
    ),
    "standup": (
        "Summarise a thread as a stand-up update",
        "Answer three questions in order: what moved, what is blocked, and what\n"
        "is next. Three bullets each at most, no preamble.",
    ),
    "triage": (
        "Triage a bug report into a severity and an owner",
        "Restate the failure in one line, judge severity against user impact,\n"
        "then name the area of the codebase most likely responsible.",
    ),
}


def write_skill_files(skills_dir: Path) -> None:
    for name, (description, instructions) in DEMO_SKILLS.items():
        folder = skills_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n{instructions}\n",
            encoding="utf-8",
        )


# --------------------------------------------------------------------------
# An autonomous agent whose deliverable was refined over two follow-ups, so the
# cockpit shows a result with three versions and three folded turns.
# --------------------------------------------------------------------------
_GUIDE_SERVICES = (
    "- **Gateway** — every request enters here; it owns rate limiting.\n"
    "- **Ledger** — the source of truth for billing events.\n"
    "- **Notifier** — email and push, driven by the event bus.\n"
)
_GUIDE_V1 = (
    "# Platform onboarding guide\n\n"
    "## Week one\n"
    "1. Request access to the cloud console, the deploy pipeline and the on-call rota.\n"
    "2. Clone the service catalogue and run `make dev` for the gateway.\n"
    "3. Pair with your buddy on one small fix and ship it.\n\n"
    "## The services you'll touch first\n" + _GUIDE_SERVICES + "\n"
    "## How we ship\n"
    "Small pull requests, reviewed within a day. A merge to `main` deploys to staging; "
    "production releases go out every Tuesday."
)
_GUIDE_V2 = (
    _GUIDE_V1 + "\n\n## Joining on-call\n"
    "- Shadow two rotations before you carry the pager.\n"
    "- Every alert links a runbook. If one doesn't, write it after the incident.\n"
    "- Hand over at 10:00 on Mondays with a written summary in #platform-oncall."
)
_GUIDE_V3 = (
    "# Platform onboarding guide\n\n"
    "## First-week checklist\n"
    "- [ ] Access: cloud console, deploy pipeline, on-call rota\n"
    "- [ ] Run the gateway locally with `make dev`\n"
    "- [ ] Ship one small fix with your buddy\n"
    "- [ ] Read the Gateway, Ledger and Notifier runbooks\n"
    "- [ ] Shadow one on-call handover\n\n"
    "## The services\n" + _GUIDE_SERVICES + "\n"
    "## Shipping and on-call\n"
    "Small pull requests, reviewed within a day. `main` deploys to staging and production "
    "ships on Tuesdays. Shadow two on-call rotations before you carry the pager."
)
_GUIDE_TURNS = [
    {
        "at": {"hours": 3},
        "prompt": (
            "Write an onboarding guide for engineers joining the platform team, "
            "from the handbook and the service catalogue."
        ),
        "steps": [
            ("say", "I'll start from the team handbook, then map the services a newcomer meets."),
            ("tool", "workspace-read_file", '{"path": "handbook/README.md"}', "412 lines read."),
            ("say", "PROGRESS: 40 | Read the handbook; mapping the services next."),
            ("nudge",),
            ("tool", "workspace-search", '{"query": "owner: platform"}', "3 services match."),
            ("think", "Lead with access and tooling, then the services, then how we ship."),
        ],
        "prose": "",
        "title": "Platform onboarding guide",
        "body": _GUIDE_V1,
        "complete": (
            "Drafted a first onboarding guide: week-one access and setup, the three "
            "services a newcomer touches first, and how the team ships."
        ),
    },
    {
        "at": {"hours": 2, "minutes": 30},
        "prompt": "Add a section on joining on-call.",
        "steps": [
            ("tool", "workspace-read_file", '{"path": "runbooks/oncall.md"}', "96 lines read."),
            ("say", "The on-call runbook covers shadowing and handover, so I'll draw on it."),
        ],
        "prose": "",
        "title": "Platform onboarding guide (with on-call)",
        "body": _GUIDE_V2,
        "complete": (
            "Added a Joining on-call section, drawn from the on-call runbook: shadowing "
            "first, runbooks for every alert, and the Monday handover."
        ),
    },
    {
        "at": {"hours": 2},
        "prompt": "Too long for day one. Make it shorter and lead with a first-week checklist.",
        "steps": [
            ("think", "Newcomers skim: move every action into a checklist, merge the prose."),
            ("say", "PROGRESS: 70 | Folded the actions into a checklist; tightening the rest."),
        ],
        "prose": (
            "I cut the guide by about half. Everything a newcomer has to *do* is now a "
            "checklist at the top; shipping and on-call share one short section."
        ),
        "title": "Platform onboarding guide — first-week checklist",
        "body": _GUIDE_V3,
        "complete": (
            "Shortened the guide by half and led with a first-week checklist; shipping "
            "and on-call now share one section."
        ),
    },
]


async def _seed_refined_agent(s: AsyncSession) -> None:
    """Add "Onboarding guide writer": one run, three prompts, three result versions."""
    last = _GUIDE_TURNS[-1]
    agent = AgentSession(
        title="Onboarding guide writer",
        task_prompt=_GUIDE_TURNS[0]["prompt"],
        status="completed",
        autonomy_enabled=True,
        max_steps=12,
        progress=100,
        model="gpt-5",
        result_summary=last["complete"],
        total_input_tokens=212_400,
        total_output_tokens=9_800,
        finished_at=ago(hours=1, minutes=56),
        last_activity_at=ago(hours=1, minutes=56),
        last_read_at=NOW,
    )
    s.add(agent)
    await s.flush()
    run = AgentRun(
        agent_id=agent.id,
        status="completed",
        model=agent.model,
        progress=100,
        result_summary=agent.result_summary,
        total_input_tokens=agent.total_input_tokens,
        total_output_tokens=agent.total_output_tokens,
        started_at=ago(hours=3),
        finished_at=agent.finished_at,
        last_activity_at=agent.finished_at,
    )
    s.add(run)
    await s.flush()
    agent.current_run_id = run.id

    events: list[AgentEvent] = []
    for n, turn in enumerate(_GUIDE_TURNS):
        start: datetime = ago(**turn["at"])
        clock = [start]

        def tick(seconds: float, clock: list[datetime] = clock) -> datetime:
            clock[0] = clock[0] + timedelta(seconds=seconds)
            return clock[0]

        events.append(AgentEvent(kind="UserMessageData", text=turn["prompt"], at=start))
        events.append(AgentEvent(kind="turn_start", at=tick(1)))
        for i, step in enumerate(turn["steps"]):
            if step[0] == "say":
                events.append(AgentEvent(kind="assistant_message", text=step[1], at=tick(6)))
            elif step[0] == "think":
                events.append(AgentEvent(kind="reasoning", text=step[1], at=tick(9)))
            elif step[0] == "nudge":
                events.append(AgentEvent(kind="turn_end", at=tick(1)))
                events.append(AgentEvent(kind="AssistantIdleData", at=tick(1)))
                events.append(AgentEvent(kind="UserMessageData", text=_CONTINUE_NUDGE, at=tick(1)))
                events.append(AgentEvent(kind="turn_start", at=tick(1)))
            else:
                _, tool, args, result = step
                request_id = f"demo-{n}-{i}"
                events.append(
                    AgentEvent(
                        kind="ToolExecutionStartData",
                        tool_name=tool,
                        request_id=request_id,
                        data={"arguments": args},
                        at=tick(4),
                    )
                )
                events.append(
                    AgentEvent(
                        kind="ToolExecutionCompleteData",
                        tool_status="done",
                        request_id=request_id,
                        data={"result": result},
                        at=tick(18),
                    )
                )
        final = "\n\n".join(
            part
            for part in (
                turn["prose"],
                f"ARTIFACT: {turn['title']}\n{turn['body']}\nEND_ARTIFACT",
                f"OBJECTIVE_COMPLETE: {turn['complete']}",
            )
            if part
        )
        events.append(
            AgentEvent(kind="usage", data={"model": "gpt-5", "output_tokens": 1800}, at=tick(20))
        )
        final_at = tick(24)
        events.append(AgentEvent(kind="assistant_message", text=final, at=final_at))
        events.append(AgentEvent(kind="turn_end", at=tick(1)))
        events.append(AgentEvent(kind="AssistantIdleData", at=tick(0.2)))
        events.append(AgentEvent(kind="idle", at=tick(0.1)))
        # What the goal loop publishes when the turn settles: the ARTIFACT block as
        # an output, and the completion as the auto-captured result.
        s.add_all(
            [
                AgentArtifact(
                    agent_id=agent.id,
                    agent_run_id=run.id,
                    key="output",
                    kind="text",
                    title=turn["title"],
                    content=turn["body"],
                    created_at=tick(0.1),
                ),
                AgentArtifact(
                    agent_id=agent.id,
                    agent_run_id=run.id,
                    key="result",
                    kind="text",
                    title="Result",
                    content=strip_control_directives(final),
                    created_at=tick(0.1),
                ),
            ]
        )

    for event in events:
        event.agent_run_id = run.id
        s.add(
            AgentEventRecord(
                agent_session_id=agent.id,
                agent_run_id=run.id,
                payload=event.model_dump_json(),
                created_at=event.at,
            )
        )


async def seed() -> None:
    await init_db()

    async with SessionLocal() as s:
        # Screenshots render archived fixtures, never a real Copilot session.
        await s.merge(AppSetting(key="agents_enabled", value="false"))

        # ---------------- roles ----------------
        # The built-in `default` role is seeded by a migration; only add extras.
        s.add_all(
            [
                Role(
                    name="Reviewer",
                    system_prompt=(
                        "You review work critically but kindly. Lead with what is wrong "
                        "and why it matters, then say what you would do instead."
                    ),
                ),
                Role(
                    name="Technical writer",
                    system_prompt=(
                        "You write documentation. Prefer the shortest sentence that is "
                        "still precise, explain the why before the how, and never invent "
                        "an option that does not exist."
                    ),
                ),
            ]
        )

        # ---------------- collections ----------------
        platform = Collection(name="Platform", slug="platform")
        personal = Collection(name="Personal", slug="personal")
        s.add_all([platform, personal])
        await s.flush()

        # ---------------- topics ----------------
        t_release = Topic(
            title="Release 2026.8 — cut the notes",
            slug="release-2026-8",
            description="Everything that has to happen before the August build ships.",
            collection_id=platform.id,
            github_repo="acme/widget-platform",
            github_issue_number=482,
            pinned=True,
        )
        t_onboarding = Topic(
            title="Onboarding checklist for new hires",
            slug="onboarding-checklist",
            description="The list we hand someone on day one.",
            collection_id=platform.id,
        )
        t_perf = Topic(
            title="Search latency regression",
            slug="search-latency-regression",
            description="p95 doubled after the indexing change.",
            collection_id=platform.id,
            github_repo="acme/widget-platform",
            github_issue_number=511,
        )
        t_digest = Topic(
            title="Weekly engineering digest",
            slug="weekly-engineering-digest",
            description="Runs itself every Monday morning and posts the week's summary.",
            kind="scheduled",
            collection_id=platform.id,
        )
        t_reading = Topic(
            title="Reading list",
            slug="reading-list",
            description="Papers and posts worth a second pass.",
            collection_id=personal.id,
        )
        s.add_all([t_release, t_onboarding, t_perf, t_digest, t_reading])
        await s.flush()

        # A child topic, so the tree shows nesting.
        s.add(
            Topic(
                title="Migration notes",
                slug="release-2026-8-migration-notes",
                description="Breaking changes that need a callout in the notes.",
                parent_id=t_release.id,
                collection_id=platform.id,
            )
        )

        # ---------------- an edited topic summary awaiting review ----------------
        # The release topic carries a hand-edited brief plus a proposal, so the
        # screenshot shows the per-change review the panel is built around.
        s.add(
            TopicSummary(
                topic_id=t_release.id,
                visible=True,
                user_edited=True,
                model="gpt-4.1",
                generated_at=ago(days=1),
                content=(
                    "## Status\n"
                    "- Release branch cut; QA is running the regression pass.\n"
                    "- Migration notes still missing the storage callout.\n\n"
                    "## Actions\n"
                    "- [ ] Ask @dana for the signing certificate\n"
                    "- [ ] Write the storage migration callout\n"
                    "- [x] Tag rc2\n\n"
                    "## Key information\n"
                    "- Ship window closes Friday 18:00 CET\n"
                    "- Rollback is `widget-platform@2026.7.3`"
                ),
                pending_model="gpt-4.1",
                pending_generated_at=NOW,
                pending_content=(
                    "## Status\n"
                    "- Release branch cut; QA finished the regression pass with 2 "
                    "known issues.\n"
                    "- Migration notes still missing the storage callout.\n\n"
                    "## Actions\n"
                    "- [x] Ask @dana for the signing certificate\n"
                    "- [ ] Write the storage migration callout\n"
                    "- [x] Tag rc2\n\n"
                    "## Key information\n"
                    "- Ship window closes Friday 18:00 CET\n"
                    "- Rollback is `widget-platform@2026.7.3`"
                ),
            )
        )

        # ---------------- a scheduled topic ----------------
        # Two recurrence rules, so the screenshot shows a schedule that combines
        # cadences: the Monday morning digest plus a Friday wrap-up.
        digest_schedule = TopicSchedule(
            topic_id=t_digest.id,
            enabled=True,
            clear_context=True,
            prompt=(
                "Summarise what changed in the platform this week: merged pull "
                "requests, closed issues, and anything still blocked. Lead with the "
                "things a reader has to act on."
            ),
            next_run_at=NOW + timedelta(days=2, hours=3),
            last_run_at=ago(days=5),
            status="idle",
        )
        digest_schedule.set_recurrence_rules(
            [
                RecurrenceRule(
                    interval_seconds=604_800,
                    run_at_minute=9 * 60,
                    timezone="Europe/Paris",
                    days_of_week=1,  # Monday
                ),
                RecurrenceRule(
                    interval_seconds=604_800,
                    run_at_minute=17 * 60,
                    timezone="Europe/Paris",
                    days_of_week=1 << 4,  # Friday
                ),
            ]
        )
        s.add(digest_schedule)

        # ---------------- messages ----------------
        s.add_all(
            [
                Message(
                    topic_id=t_release.id,
                    role=MessageRole.USER,
                    content=(
                        "We branch for 2026.8 on Thursday. What still has to land, and "
                        "what should the release notes lead with?"
                    ),
                    created_at=ago(hours=3),
                ),
                Message(
                    topic_id=t_release.id,
                    role=MessageRole.ASSISTANT,
                    content=(
                        "Three things are still open on the milestone:\n\n"
                        "- **#488 — per-step tool scoping.** Merged behind a flag; the "
                        "flag still defaults off, so either flip it or leave it out of "
                        "the notes.\n"
                        "- **#502 — token meter on the run header.** In review, small.\n"
                        "- **#511 — search latency.** Not a blocker for the branch, but "
                        "it is the regression people will ask about.\n\n"
                        "For the notes, I would lead with tool scoping: it is the only "
                        "change that alters what a run costs, and the measured drop was "
                        "large enough that people will notice it on their bill."
                    ),
                    created_at=ago(hours=3),
                ),
                Message(
                    topic_id=t_digest.id,
                    role=MessageRole.USER,
                    content=(
                        "Summarise what changed in the platform this week: merged pull "
                        "requests, closed issues, and anything still blocked."
                    ),
                    created_at=ago(days=5),
                ),
                Message(
                    topic_id=t_digest.id,
                    role=MessageRole.ASSISTANT,
                    content=(
                        "**Week of 11 August**\n\n"
                        "- **Per-step tool scoping (#488)** — a workflow step can now name "
                        "the MCP servers it may use. Measured on the six-step briefing "
                        "run, this cut tool-turn tokens by roughly 95%.\n"
                        "- **Replay a single step (#497)** — re-run one step on the exact "
                        "input it first saw, without re-driving the pipeline around it.\n"
                        "- **Search latency (#511)** — still open. The cause is a "
                        "synonym-expansion pass that moved above the cache lookup; the "
                        "fix is not merged yet.\n\n"
                        "Nothing is blocked on a decision this week."
                    ),
                    created_at=ago(days=5),
                ),
                Message(
                    topic_id=t_perf.id,
                    role=MessageRole.USER,
                    content="p95 went from 180ms to 410ms after the indexing change. Where do I look first?",
                    created_at=ago(days=1, hours=2),
                ),
                Message(
                    topic_id=t_perf.id,
                    role=MessageRole.ASSISTANT,
                    content=(
                        "The shape of that jump — roughly 2.3x, flat across query types — "
                        "points at a per-query fixed cost rather than a worse plan. The "
                        "indexing change added a synonym-expansion pass that runs before "
                        "the cache lookup, so every query pays for it even on a hit.\n\n"
                        "Check `search/expand.py` first: if the expansion moved above the "
                        "cache check, moving it back below should recover most of it."
                    ),
                    created_at=ago(days=1, hours=2),
                ),
            ]
        )

        # ---------------- chats ----------------
        c_regex = Chat(title="Regex for semver tags", slug="regex-for-semver-tags")
        c_sql = Chat(title="Postgres index for a range query", slug="postgres-range-index")
        c_rewrite = Chat(
            title="Rewrite anything I paste",
            slug="rewrite-anything-i-paste",
            description="For every message I send: /rewrite",
            description_as_system_prompt=True,
        )
        s.add_all([c_regex, c_sql, c_rewrite])
        await s.flush()
        s.add_all(
            [
                Message(
                    chat_id=c_regex.id,
                    role=MessageRole.USER,
                    content="Regex that matches a semver tag like v2026.8.1 but not v2026.8",
                    created_at=ago(hours=6),
                ),
                Message(
                    chat_id=c_regex.id,
                    role=MessageRole.ASSISTANT,
                    content=(
                        "```\n^v\\d+\\.\\d+\\.\\d+$\n```\n\n"
                        "Anchoring both ends is what rejects `v2026.8` — without `$` the "
                        "two-part tag would match as a prefix."
                    ),
                    created_at=ago(hours=6),
                ),
            ]
        )

        # ---------------- memories ----------------
        s.add_all(
            [
                Memory(kind="preference", content="Prefer British spelling in anything published."),
                Memory(
                    kind="context",
                    content="The platform team owns acme/widget-platform; releases are CalVer.",
                ),
                Memory(
                    kind="preference",
                    content="Keep summaries under 150 words unless I ask for the long version.",
                ),
                Memory(
                    kind="fact",
                    content="Our staging environment is rebuilt nightly, so never treat its data as durable.",
                ),
            ]
        )

        # ---------------- skills (DB enablement mirrors the files) ----------------
        for name, (description, instructions) in DEMO_SKILLS.items():
            s.add(
                Skill(
                    name=name,
                    description=description,
                    instructions=instructions,
                    enabled=name != "triage",  # one disabled, so the toggle reads
                    migrated=True,
                )
            )

        # ---------------- the workflow (all four step kinds) ----------------
        # Reusable agents the pipeline's steps point at. A step showing "Agent
        # missing" is what you get without these, so they matter for the shot.
        a_survey = AgentSession(
            title="Surveyor",
            task_prompt=(
                "Survey a subject and report what you find as a compact, factual list. "
                "Prefer specifics (names, dates, identifiers) over prose."
            ),
            status="idle",
            model="gpt-5",
            total_input_tokens=96_400,
            total_output_tokens=12_800,
            last_activity_at=ago(days=1),
        )
        a_writer = AgentSession(
            title="Digest writer",
            task_prompt=(
                "Turn a list of findings into a short written digest. Lead with what "
                "changed for the reader, then the detail."
            ),
            status="idle",
            model="gpt-5",
            total_input_tokens=141_900,
            total_output_tokens=24_300,
            last_activity_at=ago(days=1),
        )
        a_editor = AgentSession(
            title="Editor",
            task_prompt=(
                "Check a draft against its source material. Vote PASS only when every "
                "claim traces to the source."
            ),
            status="idle",
            model="gpt-5-mini",
            total_input_tokens=63_100,
            total_output_tokens=3_200,
            last_activity_at=ago(days=1),
        )
        a_owner = AgentSession(
            title="Code owner finder",
            task_prompt="Given a failure, name the area of the codebase most likely responsible.",
            status="idle",
            model="gpt-5-mini",
            total_input_tokens=18_700,
            total_output_tokens=2_100,
            last_activity_at=ago(hours=5),
        )
        a_standalone = AgentSession(
            title="Release notes helper",
            task_prompt="Summarise the user-facing changes in a release.",
            status="completed",
            model="gpt-5-mini",
            result_summary="The release notes are ready, grouped by user impact.",
            last_activity_at=ago(hours=2),
        )
        # Private vessels behind the two inline steps — hidden from the roster.
        v_publish = AgentSession(
            title="Publish",
            task_prompt="Post the approved digest to the engineering topic.",
            inline=True,
            status="idle",
            model="gpt-5-mini",
        )
        v_classify = AgentSession(
            title="Classify",
            task_prompt="Restate the failure in one line and judge its severity.",
            inline=True,
            status="idle",
            model="gpt-5-mini",
        )
        v_reply = AgentSession(
            title="Draft the reply",
            task_prompt="Write a reply that acknowledges the report and states the next step.",
            inline=True,
            status="idle",
            model="gpt-5-mini",
        )
        s.add_all(
            [a_survey, a_writer, a_editor, a_owner, a_standalone, v_publish, v_classify, v_reply]
        )
        await s.flush()

        writer_run = AgentRun(
            agent_id=a_writer.id,
            status="idle",
            model=a_writer.model,
            started_at=ago(days=1, minutes=2),
            finished_at=ago(days=1),
            last_activity_at=ago(days=1),
        )
        s.add(writer_run)
        await s.flush()
        a_writer.current_run_id = writer_run.id

        for event in [
            AgentEvent(
                kind="user_message",
                text="Draft this week's engineering digest from the release survey.",
                at=ago(days=1, minutes=2),
            ),
            AgentEvent(
                kind="assistant_reasoning",
                text=(
                    "I will group the survey by user impact, distinguish shipped changes "
                    "from investigations, and keep the digest short."
                ),
                at=ago(days=1, minutes=1),
            ),
            AgentEvent(
                kind="assistant_message",
                text=(
                    "## This week in the platform\n\n"
                    "- **Tool scoping:** each agent can now use a focused set of MCP servers.\n"
                    "- **Workflow replay:** revisit a step without advancing the pipeline.\n"
                    "- **Search performance:** the regression is diagnosed; a fix is still "
                    "in progress.\n\n"
                    "The draft is ready for review before publishing.\n\n"
                    "```suggest\nMake it shorter\nAdd a next-steps section\n```"
                ),
                at=ago(days=1),
            ),
        ]:
            event.agent_run_id = writer_run.id
            s.add(
                AgentEventRecord(
                    agent_session_id=a_writer.id,
                    agent_run_id=writer_run.id,
                    payload=event.model_dump_json(),
                    created_at=event.at,
                )
            )

        await _seed_refined_agent(s)

        wf = Workflow(
            name="Weekly release digest",
            description=(
                "Survey what merged, draft the digest, have it checked, get a human "
                "sign-off, then publish it."
            ),
            icon="📰",
            color="indigo",
            status="idle",
            run_count=7,
            last_run_at=ago(days=1),
            finished_at=ago(days=1),
            max_loops=3,
            step_timeout_seconds=1800,
            schedule_enabled=True,
            interval_seconds=604_800,
            run_at_minute=8 * 60 + 30,
            timezone="Europe/Paris",
            days_of_week=1,
            next_run_at=NOW + timedelta(days=6),
            result_summary="Published the 2026.8 digest after one editorial loop-back.",
        )
        s.add(wf)
        await s.flush()

        steps = [
            WorkflowStep(
                workflow_id=wf.id,
                position=0,
                kind="task",
                agent_id=a_survey.id,
                name="Survey what merged",
                instructions=(
                    "List everything merged since {{state.last_digest_at | the beginning "
                    "of time}}. Facts only — number, title, author."
                ),
                on_error="retry",
                max_retries=2,
                context_mode="none",
            ),
            WorkflowStep(
                workflow_id=wf.id,
                position=1,
                kind="task",
                agent_id=a_writer.id,
                name="Draft the digest",
                instructions=(
                    "Turn {{step.0.output}} into a digest for "
                    "{{state.audience | a general technical audience}}. Lead with what "
                    "readers must act on."
                ),
                on_error="fail",
                context_mode="auto",
            ),
            WorkflowStep(
                workflow_id=wf.id,
                position=2,
                kind="gate",
                agent_id=a_editor.id,
                name="Check it is accurate",
                instructions=(
                    "Verify every claim traces to the survey. FAIL if anything is "
                    "asserted that the survey does not support."
                ),
                on_fail_position=1,
                context_mode="auto",
            ),
            WorkflowStep(
                workflow_id=wf.id,
                position=3,
                kind="approval",
                name="Sign off before publishing",
                instructions="Last read before this goes to the whole engineering list.",
                on_reject="rework",
            ),
            WorkflowStep(
                workflow_id=wf.id,
                position=4,
                kind="inline",
                agent_id=v_publish.id,
                name="Publish",
                instructions="Post the approved digest to the engineering topic.",
                on_error="continue",
                context_mode="auto",
            ),
        ]
        s.add_all(steps)

        # Pipeline state — the cursor plus an operator-tunable knob.
        s.add_all(
            [
                WorkflowState(
                    workflow_id=wf.id,
                    key="last_digest_at",
                    value="2026-08-11T08:30:00Z",
                ),
                WorkflowState(
                    workflow_id=wf.id,
                    key="audience",
                    value="platform engineers who did not follow the week's PRs",
                ),
                WorkflowState(
                    workflow_id=wf.id,
                    key="seen_pr_ids",
                    value='["488","492","497","502","508"]',
                ),
            ]
        )

        # A finished run whose gate looped back once, so the trace shows an
        # `attempt 2` badge next to a normal sequence.
        run = WorkflowRun(
            workflow_id=wf.id,
            run_number=7,
            status="completed",
            trigger="schedule",
            started_at=ago(days=1, minutes=14),
            finished_at=ago(days=1),
            result_summary="Published the 2026.8 digest after one editorial loop-back.",
            total_input_tokens=48_210,
            total_output_tokens=6_940,
        )
        s.add(run)
        await s.flush()

        trace = [
            (
                "Survey what merged",
                "task",
                1,
                "completed",
                "Run brief: none (scheduled).",
                "17 pull requests merged since 11 Aug: #488 per-step tool scoping, "
                "#492 run token meter, #497 replay a single step, #502 …",
                None,
                6_120,
                980,
            ),
            (
                "Draft the digest",
                "task",
                1,
                "completed",
                "Survey of 17 merged pull requests.",
                "**This week in the platform** — tool scoping lands, replay arrives, "
                "and search gets slower before it gets faster…",
                None,
                9_450,
                1_610,
            ),
            (
                "Check it is accurate",
                "gate",
                1,
                "completed",
                "The drafted digest plus the survey it came from.",
                None,
                "FAIL: the draft claims search latency was fixed; the survey only "
                "shows the diagnosis merged.",
                7_880,
                240,
            ),
            (
                "Draft the digest",
                "task",
                2,
                "completed",
                "Gate critique: the draft claims search latency was fixed…",
                "**This week in the platform** — tool scoping lands, replay arrives, "
                "and we found the cause of the search regression…",
                None,
                10_240,
                1_720,
            ),
            (
                "Check it is accurate",
                "gate",
                2,
                "completed",
                "The revised digest plus the survey it came from.",
                None,
                "PASS: every claim traces to the survey.",
                8_010,
                250,
            ),
            (
                "Sign off before publishing",
                "approval",
                1,
                "completed",
                "Last read before this goes to the whole engineering list.",
                "Approved — 'ship it, but call the search item a diagnosis not a fix'.",
                None,
                0,
                0,
            ),
            (
                "Publish",
                "inline",
                1,
                "completed",
                "The approved digest, plus the reviewer's note.",
                "Posted to the engineering digest topic.",
                None,
                6_510,
                2_140,
            ),
        ]
        base = ago(days=1, minutes=14)
        agent_for_label = {
            "Survey what merged": a_survey.id,
            "Draft the digest": a_writer.id,
            "Check it is accurate": a_editor.id,
            "Publish": v_publish.id,
        }
        for i, (label, kind, attempt, status, ctx, out, verdict, tin, tout) in enumerate(trace):
            s.add(
                WorkflowRunStep(
                    run_id=run.id,
                    position=[st.name for st in steps].index(label),
                    kind=kind,
                    label=label,
                    agent_id=agent_for_label.get(label),
                    attempt=attempt,
                    status=status,
                    input_context=ctx,
                    output_summary=out,
                    gate_verdict=verdict,
                    input_tokens=tin,
                    output_tokens=tout,
                    started_at=base + timedelta(minutes=2 * i),
                    finished_at=base + timedelta(minutes=2 * i + 1, seconds=40),
                )
            )

        # A second workflow so the gallery isn't a single card.
        wf2 = Workflow(
            name="Triage inbound bug reports",
            description="Classify a new report, find the likely owner, and draft the reply.",
            icon="🐞",
            color="rose",
            status="idle",
            run_count=23,
            last_run_at=ago(hours=5),
            max_loops=3,
        )
        s.add(wf2)
        await s.flush()
        s.add_all(
            [
                WorkflowStep(
                    workflow_id=wf2.id,
                    position=0,
                    kind="inline",
                    agent_id=v_classify.id,
                    name="Classify",
                    instructions="Restate the failure in one line and judge its severity.",
                ),
                WorkflowStep(
                    workflow_id=wf2.id,
                    position=1,
                    kind="task",
                    agent_id=a_owner.id,
                    name="Find the owner",
                    instructions="Name the area of the codebase most likely responsible.",
                ),
                WorkflowStep(
                    workflow_id=wf2.id,
                    position=2,
                    kind="inline",
                    agent_id=v_reply.id,
                    name="Draft the reply",
                    instructions="Write a reply that acknowledges the report and states the next step.",
                ),
            ]
        )

        meeting = MeetingSession(
            title="Weekly platform sync",
            slug="weekly-platform-sync",
            status="ended",
            language="en-US",
            started_at=ago(hours=2),
            ended_at=ago(hours=1),
            speaker_names_json='{"Guest-1":"Alex","Guest-2":"Sam"}',
            summary="Add bounded retries and publish the release checklist.",
        )
        s.add(meeting)
        await s.flush()
        for offset, speaker, text in [
            (0, "Guest-1", "Let's review the latency regression and agree on next steps."),
            (14000, "Guest-2", "The retries amplify load when the gateway is slow."),
            (41000, "Guest-1", "Let's cap the retries and add jitter to the backoff."),
            (55000, "Guest-2", "I'll add the regression test and update the release checklist."),
        ]:
            s.add(
                MeetingSegment(
                    session_id=meeting.id, speaker_label=speaker, text=text, offset_ms=offset
                )
            )
        s.add(
            MeetingInsight(
                session_id=meeting.id,
                kind="action_item",
                content="Sam: add a bounded-retry regression test and update the release checklist.",
            )
        )
        workspace_dir = Path(os.environ["PRECURSOR_DATA_DIR"]) / "workspaces" / "design-notes"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "README.md").write_text(
            "# Design notes\n\n"
            "Working notes for the platform, kept alongside the topics that drive decisions.\n\n"
            "## Release checklist\n\n"
            "- Review the latency regression.\n"
            "- Bound retries and add jitter.\n"
            "- Publish the engineering digest.\n",
            encoding="utf-8",
        )
        s.add(Workspace(name="Design notes", slug="design-notes", kind="local"))

        await s.commit()

    print("Seeded the demo database.")


if __name__ == "__main__":
    skills_dir = os.environ.get("PRECURSOR_SKILLS_DIR", "")
    if skills_dir:
        write_skill_files(Path(skills_dir))
    asyncio.run(seed())
