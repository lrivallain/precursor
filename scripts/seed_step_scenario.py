"""Seed a synthetic "Step by step" acceptance scenario into a dev instance.

A wholly fictional retailer plans to move a custom domain between two Azure
Front Door Premium profiles. Everything here is invented test data, labelled as
such: no real customer, no real email, nothing sent, no Azure call. Feasibility
claims are scenario inputs to investigate, not statements about Azure.

Run from the repository root, against the dev database only (``./precursor.db``):

    uv run --frozen python scripts/seed_step_scenario.py

It only ever adds rows through the service layer (no deletes, no model calls),
and refuses to run when the database is not this checkout's own ``precursor.db``
or when a scenario topic already exists.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from precursor.backend.config import get_settings  # noqa: E402
from precursor.backend.db import SessionLocal, init_db  # noqa: E402
from precursor.backend.models.message import Message, MessageRole  # noqa: E402
from precursor.backend.models.topic import Topic  # noqa: E402
from precursor.backend.schemas.step_plan import (  # noqa: E402
    Assessment,
    StepCreate,
    StepPlanCreate,
    StepPlanUpdate,
)
from precursor.backend.services import step_plans as svc  # noqa: E402
from precursor.backend.services.collections import resolve_collection_id  # noqa: E402
from precursor.backend.services.slugs import allocate_unique_slug, slugify  # noqa: E402

TOPIC_TITLE = "[SYNTHETIC] Contoso Retail: move custom domain between Front Door profiles"
LABEL = "[ILLUSTRATIVE seeded fixture, not a model evaluation]"


def _guard() -> None:
    url = get_settings().database_url
    expected = (REPO_ROOT / "precursor.db").as_posix()
    if not url.endswith(expected) and url != "sqlite+aiosqlite:///./precursor.db":
        raise SystemExit(f"Refusing: database is {url!r}, expected this checkout's precursor.db.")
    if Path.cwd().resolve() != REPO_ROOT:
        raise SystemExit("Run this from the repository root.")


INITIAL_EMAIL = """\
**[SYNTHETIC TEST DATA: fictional retailer, invented email]**

From: Dana Okafor, Head of Digital Platforms, Contoso Retail (fictional)
Subject: Moving shop.contoso-retail.example to our new Front Door profile

Hello,

We are consolidating our web estate. `shop.contoso-retail.example` is served by
Azure Front Door Premium profile **fd-legacy** and we want it on the new profile
**fd-platform** (same subscription, different resource group). Our leadership
needs answers to the following before they approve a date:

1. Can we do this with **zero downtime**?
2. Which **steps** are involved, and in which order?
3. How is **DNS** affected, and who must act on it?
4. What is the **rollback** plan if something goes wrong?
5. What is a realistic **change window**, and what could make it longer?
6. What do you need from us (access, owners, evidence) to proceed?

We expect peak traffic on the shop during a promotion in three weeks.

Thanks,
Dana
"""

PRELIMINARY_REPLY = """\
**[SYNTHETIC: preliminary draft reply, unverified]**

Hi Dana,

A first view, to be confirmed. These points rest on **assumptions we have not
verified yet**:

- *Assumption A:* the custom domain can be associated with the second profile
  without first removing it from the first one. **(Unverified; to check.)**
- *Assumption B:* the managed certificate can be re-issued on the new profile
  without a validation gap. **(Unverified; to check.)**
- *Assumption C:* the DNS zone is managed by your team and TTLs are short
  enough for a quick switch. **(Unknown; we have not seen the zone.)**
- *Assumption D:* a rollback is simply "point DNS back". **(Unverified; it
  depends on A and B.)**

Proposed outline: prepare the new profile in parallel, validate it with a test
hostname, switch DNS, observe, keep the old profile as the fallback. We would
like to confirm the facts above before promising zero downtime.

Regards (draft, not sent)
"""

FOLLOW_UP = """\
**[SYNTHETIC TEST DATA: fictional follow-up that changes the constraints]**

Hi, Dana again. New information from leadership and our network team:

- Leadership now requires **zero downtime** and a hard **four-hour change
  window** (Saturday 02:00-06:00, local time). No extension.
- Our **DNS owner (Sam Rivera, fictional) is available only between 02:00 and
  03:00**, then goes off shift. After that, DNS changes need a ticket and take
  hours.
- It is **not clear who owns the rollback decision**: platform, security, or
  the business owner of the shop.
- Security asks that the **certificate never be served as invalid**, even
  briefly.
- The promotion starts in three weeks; if the migration slips we must freeze.

Please send a customer-ready decision package: an executive reply, an options
and trade-off matrix, a cutover and rollback runbook, risks and open decisions,
and an acceptance checklist.
"""

PLAN_ASK = (
    "[SYNTHETIC TEST DATA] Contoso Retail (fictional) must move "
    "shop.contoso-retail.example from Azure Front Door Premium profile fd-legacy to "
    "fd-platform. Produce a customer-ready decision package: executive reply, "
    "options/trade-off matrix, cutover and rollback runbook, risks and open decisions, "
    "acceptance checklist.\n\nAsks: zero downtime; steps and order; DNS impact and "
    "owner; rollback plan; realistic change window; what is needed from the customer.\n\n"
    "Constraints (latest): four-hour window Saturday 02:00-06:00, no extension; DNS "
    "owner available only 02:00-03:00; rollback owner unclear; certificate must never "
    "be served as invalid; promotion in three weeks.\n\n"
    "Do not invent feasibility guarantees: mark every unverified claim as an assumption."
)

OUTPUT_INSTRUCTIONS = (
    "A customer-ready decision package with these sections: 1. Executive reply, "
    "2. Options and trade-off matrix, 3. Cutover and rollback runbook (owners, stop "
    "conditions), 4. Risks and open decisions, 5. Acceptance checklist. Label every "
    "unverified claim as an assumption."
)

STEPS: list[tuple[str, str]] = [
    (
        "Map every ask and constraint",
        "List each ask and each constraint from the initial email and the follow-up, "
        "flag which changed, and note conflicts between them.",
    ),
    (
        "Examine assumptions and evidence",
        "For each assumption in the preliminary reply (A-D), say what evidence exists, "
        "what is missing and who can confirm it. Treat all of them as unverified.",
    ),
    (
        "Compare candidate approaches",
        "Compare at least three approaches (for example a parallel-profile switch, a "
        "staged hostname move, a maintenance-window cutover) against the constraints. "
        "No feasibility guarantees: say what must be verified first.",
    ),
    (
        "Recommend an approach with decision gates",
        "Recommend one approach, subject to explicit decision gates (go/no-go points, "
        "who decides, what evidence is required).",
    ),
    (
        "Write the cutover and rollback runbook",
        "Time-boxed runbook within the 02:00-06:00 window and the DNS owner's 02:00-03:00 "
        "availability, with an owner per step, stop conditions and rollback triggers.",
    ),
    (
        "Check consistency and coverage",
        "Check the proposed response against every ask and constraint; list omissions "
        "and contradictions between steps.",
    ),
    (
        "Assemble the customer-facing package",
        "Assemble the final package from the approved artifacts only, in the structure "
        "requested. The consolidation step can perform this assembly.",
    ),
]

ART_MAP = """\
# Asks and constraints (v1, validated by a human in this fixture)

| # | Item | Source | Changed by follow-up? |
|---|------|--------|-----------------------|
| 1 | Zero downtime | Email 1 (ask) | **Yes**: now a hard requirement |
| 2 | Steps and order | Email 1 | No |
| 3 | DNS impact and owner | Email 1 | **Yes**: owner only 02:00-03:00 |
| 4 | Rollback plan | Email 1 | **Yes**: rollback owner unclear |
| 5 | Realistic change window | Email 1 | **Yes**: fixed 4 h, 02:00-06:00, no extension |
| 6 | Needs from the customer | Email 1 | No |
| 7 | Certificate never served invalid | Follow-up | New |
| 8 | Promotion in three weeks | Both | Unchanged, deadline pressure |

**Tension:** a hard 4 h window plus a DNS owner available for 1 h means the DNS
change must happen early in the window, leaving rollback needing DNS access
after 03:00 that the owner cannot provide.
"""

ART_EVIDENCE_V1 = """\
# Assumptions and evidence (model draft)

| Assumption | Evidence so far | Missing | Who can confirm |
|-----------|-----------------|---------|-----------------|
| A. Domain can be associated with the second profile without removal from the first | None in this fixture | Product documentation check, lab test | Platform engineer |
| B. Managed certificate re-issuance without a validation gap | None | Certificate behaviour on the new profile | Security + platform |
| C. DNS zone is customer-managed with short TTLs | Synthetic note: "TTL 300 s" in a made-up screenshot **(invented evidence)** | Actual zone export | Sam Rivera (DNS owner) |
| D. Rollback is "point DNS back" | None | Depends on A and B | Rollback owner (unassigned) |

All four remain **unverified**. Do not state zero downtime as achievable until A
and B are proven in a test.
"""

ART_COMPARE_V1 = """\
# Candidate approaches (model draft)

| Approach | Downtime risk | Fits 4 h window | Needs DNS owner after 03:00 | Open facts |
|----------|---------------|-----------------|------------------------------|------------|
| 1. Parallel profile, DNS switch | Low *(if A and B hold)* | Yes | Only for rollback | A, B, C |
| 2. Staged hostname move | Low-medium | Maybe | Yes | A, B |
| 3. Maintenance-window cutover | Medium | Yes | Yes | None identified |
"""

ART_COMPARE_USER_EDIT = (
    ART_COMPARE_V1
    + """
## My notes (human edit)

- Approach 2 looks too slow for the window. Drop it unless the team shows otherwise.
- TODO: add a **cost / effort** column and the effect of the promotion freeze.
"""
)

ART_RUNBOOK = """\
# Cutover and rollback runbook (skeleton, work in progress)

| Time | Action | Owner | Stop condition |
|------|--------|-------|----------------|
| 02:00 | Confirm go/no-go gate 1 | TBD (decision owner unclear) | Evidence for A/B missing |
| 02:10 | ... | ... | ... |

TODO: fill in the timeline, name the owners, and decide who can order a rollback.
"""

ILLUSTRATIVE_STEP = {
    "map": Assessment(
        score=88,
        summary=f"{LABEL} Looks complete against the two emails.",
        covered=["All six asks from the first email", "All five follow-up constraints"],
        evidence=["Initial email", "Follow-up message"],
        assumptions=["The follow-up supersedes the first email where they conflict"],
        gaps=["Cost and effort are not mentioned by the customer"],
    ),
    "evidence": Assessment(
        score=64,
        summary=f"{LABEL} Honest about gaps; nothing is proven.",
        covered=["Assumptions A-D reviewed"],
        evidence=["Preliminary reply"],
        assumptions=["The TTL note is synthetic and not real evidence"],
        gaps=["No lab test of A or B", "No DNS zone export", "No rollback owner"],
    ),
}

ILLUSTRATIVE_PLAN = Assessment(
    score=41,
    summary=f"{LABEL} Early stage: only the first steps are drafted.",
    covered=["Asks and constraints mapped", "Assumptions listed"],
    evidence=["Step 1 (validated)", "Step 2 (draft)"],
    assumptions=["Zero downtime is possible: unverified"],
    gaps=["No recommendation yet", "No runbook owners", "Rollback ownership undecided"],
    contradictions=["4 h window vs DNS owner available 1 h: rollback has no DNS access"],
)


async def _topic(session) -> Topic:  # type: ignore[no-untyped-def]
    existing = (
        await session.execute(select(Topic).where(Topic.title == TOPIC_TITLE))
    ).scalar_one_or_none()
    if existing is not None:
        raise SystemExit(f"Already seeded: topic #{existing.id} exists. Nothing written.")
    topic = Topic(
        title=TOPIC_TITLE,
        slug=await allocate_unique_slug(session, slugify(TOPIC_TITLE) or "topic", Topic),
        description=(
            "Synthetic acceptance fixture for Step by step. Fictional customer and emails; "
            "invented evidence is labelled; nothing here is real or sent."
        ),
        collection_id=await resolve_collection_id(session, None),
    )
    session.add(topic)
    await session.flush()
    for role, content in (
        (MessageRole.USER, INITIAL_EMAIL),
        (MessageRole.ASSISTANT, PRELIMINARY_REPLY),
        (MessageRole.USER, FOLLOW_UP),
    ):
        session.add(Message(topic_id=topic.id, role=role, content=content))
    await session.commit()
    return topic


async def _main_plan(session, topic: Topic) -> int:  # type: ignore[no-untyped-def]
    plan = await svc.create_plan(
        session,
        StepPlanCreate(
            title="[SYNTHETIC] Front Door decision package",
            topic_id=topic.id,
            source_text=PLAN_ASK,
            propose=False,
            automation_mode="assisted",
        ),
    )
    await svc.update_plan(session, plan.id, StepPlanUpdate(output_instructions=OUTPUT_INSTRUCTIONS))
    steps = [
        await svc.create_step(session, plan, StepCreate(title=t, instruction=i)) for t, i in STEPS
    ]
    await svc.start_plan(session, plan.id, accept_proposed=True)

    # 1: validated by a human. 2: model draft awaiting review. 3: human editing a
    # model draft. 5: a human skeleton. 4, 6, 7 stay open for the tester.
    await svc.write_artifact(session, steps[0].id, ART_MAP, author="user")
    await svc.set_step_status(session, steps[0].id, "validated")
    await svc.write_artifact(
        session, steps[1].id, ART_EVIDENCE_V1, author="model", mark="to_validate"
    )
    await svc.write_artifact(
        session, steps[2].id, ART_COMPARE_V1, author="model", mark="to_validate"
    )
    await svc.write_artifact(session, steps[2].id, ART_COMPARE_USER_EDIT, author="user")
    await svc.set_step_status(session, steps[2].id, "in_progress")
    await svc.write_artifact(session, steps[4].id, ART_RUNBOOK, author="user")

    # Illustrative scores, tied to the current content so they read as fresh.
    plan = await svc.get_plan(session, plan.id)
    by_id = {s.id: s for s in await svc.plan_steps(session, plan.id)}
    for step, key in ((steps[0], "map"), (steps[1], "evidence")):
        row = by_id[step.id]
        row.assessment_json = ILLUSTRATIVE_STEP[key].model_dump_json()
        row.assessed_sig = svc.step_signature(plan, row)
    plan.assessment_json = ILLUSTRATIVE_PLAN.model_dump_json()
    plan.assessed_sig = svc.plan_signature(plan, list(by_id.values()))
    await session.commit()
    return plan.id


async def _clean_plan(session, topic: Topic) -> int:  # type: ignore[no-untyped-def]
    plan = await svc.create_plan(
        session,
        StepPlanCreate(
            title="[SYNTHETIC] Front Door decision package (clean start)",
            topic_id=topic.id,
            source_text=PLAN_ASK,
            propose=False,
            automation_mode="manual",
        ),
    )
    await svc.update_plan(session, plan.id, StepPlanUpdate(output_instructions=OUTPUT_INSTRUCTIONS))
    for title, instruction in STEPS:
        await svc.create_step(session, plan, StepCreate(title=title, instruction=instruction))
    await svc.start_plan(session, plan.id, accept_proposed=True)
    return plan.id


async def main() -> None:
    _guard()
    await init_db()
    async with SessionLocal() as session:
        topic = await _topic(session)
        main_id = await _main_plan(session, topic)
        clean_id = await _clean_plan(session, topic)
    print(f"topic={topic.id} slug={topic.slug} main_plan={main_id} clean_plan={clean_id}")


if __name__ == "__main__":
    asyncio.run(main())
