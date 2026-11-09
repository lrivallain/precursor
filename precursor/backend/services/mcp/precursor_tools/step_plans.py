"""Step-by-step plan tools: create, inspect, amend, edit, run and pause plans."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    Message,
    MessageRole,
)
from precursor.backend.services.links import app_base_url

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import _GATED, _section_enabled, mcp

# --------------------------------------------------------------------------
# step-by-step plans (read/write)
# --------------------------------------------------------------------------
_MAX_PLAN_WAIT_SECONDS = 120


def _http_detail(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    return str(detail if detail is not None else exc)


def _plan_url(plan_id: int) -> str:
    return f"{app_base_url()}/steps/{plan_id}"


async def _app_post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """POST to the app's REST API; the error dict on failure, else ``None``.

    A stdio MCP child is not the process that owns the automated run (it lives in
    the app's event loop), so run control is delegated to the app over loopback.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if payload is None:
                resp = await client.post(f"{app_base_url()}{path}")
            else:
                resp = await client.post(f"{app_base_url()}{path}", json=payload)
    except httpx.HTTPError as exc:
        return {"error": f"The Precursor app is not reachable to change the plan: {exc}"}
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        return {"error": str(detail)}
    return None


async def _plan_summary(plan_id: int, *, include_artifacts: bool = False) -> dict[str, Any]:
    from precursor.backend.services import step_plans as svc
    from precursor.backend.services.events import is_app_process

    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        read = await svc.to_plan_read(session, plan)
        raw_state = plan.auto_state
        filed_in_current_source = await svc.final_filed_in_current_source(session, plan)
    parents = {s.parent_step_id for s in read.steps if s.parent_step_id is not None}
    leaves = [
        s for s in read.steps if s.id not in parents and s.status not in ("proposed", "cancelled")
    ]
    steps = []
    for s in read.steps:
        item: dict[str, Any] = {
            "id": s.id,
            "parent_step_id": s.parent_step_id,
            "title": s.title,
            "instruction": s.instruction,
            "status": s.effective_status,
            "score": s.assessment.score if s.assessment else None,
            "has_artifact": bool(s.artifact.strip()),
        }
        if include_artifacts:
            item["artifact"] = s.artifact
        steps.append(item)
    run_state = read.auto_state if is_app_process() else raw_state
    if read.archived_at is not None:
        note = "This plan is archived. The user must restore it before it can be amended or run."
    elif run_state == "paused":
        note = (
            "The run is paused; the revised result is not complete. Read the activity and "
            "report the blocker. Do not repeatedly resume an unresolved weak step, produce "
            "a substitute final yourself, or claim the plan is complete. A person must review "
            "a held draft before it can continue."
        )
    elif run_state == "running":
        note = (
            "The run is still in progress and drafts and checks steps one at a time, not in "
            "parallel. Validated steps and their scores are intermediate; "
            "they do not mean the final output has been assessed or filed. Do not claim a "
            "final has been delivered without a current posted version. Unless the user "
            "explicitly asks for a separate draft, do not copy step drafts into the source "
            "with append_note or post_message."
        )
        if read.source_topic_id is not None or read.source_chat_id is not None:
            note += (
                " In the linked conversation, report the plan once and end the turn rather "
                "than waiting or checking it repeatedly, unless the user asked you to wait. "
                "The plan card shows live progress."
            )
            if read.delegation_policy == "autonomous":
                note += (
                    " If the assessed final passes review, the app files it and wakes the source "
                    "conversation; otherwise the run stops with a blocker. Do not post a second copy."
                )
            else:
                note += " The final review and hand-back follow the source conversation's autonomy."
        else:
            note += " Use get_step_plan to check progress when needed."
    elif filed_in_current_source and not read.final_stale and not read.final_provisional:
        note = (
            "The app recorded this exact final in the current source conversation. Review it "
            "briefly instead of posting or repeating the entire result unless the user asks. "
            "Do not assert that a file or external search did or did not happen without evidence; "
            "the available tool records may be incomplete."
        )
    elif read.delegation_policy in ("autonomous", "balanced", "plan"):
        note = "Checked drafts are validated by the run according to the source autonomy."
    else:
        note = "Drafts wait for the user's approval."
    return {
        "plan_id": plan_id,
        "title": read.title,
        "url": _plan_url(plan_id),
        "status": read.status,
        "archived": read.archived_at is not None,
        "topic_id": read.source_topic_id,
        "chat_id": read.source_chat_id,
        "state_token": read.state_token,
        "source_text": read.source_text,
        "output_instructions": read.output_instructions,
        "amendments": [revision.model_dump(mode="json") for revision in read.amendments],
        "automation": read.automation_mode,
        "delegation_policy": read.delegation_policy,
        # Only the app process knows whether a run is really alive; a child trusts the row.
        "run_state": run_state,
        "progress": {
            "validated": sum(1 for s in leaves if s.effective_status == "validated"),
            "to_validate": sum(1 for s in leaves if s.effective_status == "to_validate"),
            "total": len(leaves),
        },
        "final_output": {
            "exists": bool((read.final_output or "").strip()),
            "provisional": read.final_provisional,
            "stale": read.final_stale,
            "version": read.final_version,
            "posted_version": read.final_posted_version,
            "filed_in_current_source": filed_in_current_source,
            "text": read.final_output if include_artifacts else None,
        },
        "assessment_score": read.assessment.score if read.assessment else None,
        "assessment_scope": read.assessment.scope if read.assessment else None,
        "assessment_stale": read.assessment_stale,
        "activity": [item.text for item in read.activity[-8:]],
        "steps": steps,
        "note": note,
    }


async def _launch_run(plan_id: int) -> dict[str, Any] | None:
    """Make the plan automated and active, then start its run (error dict on failure)."""
    from fastapi import HTTPException

    from precursor.backend.services import step_plan_runner as runner
    from precursor.backend.services.events import is_app_process, publish_step_plan_changed

    if not is_app_process():
        return await _app_post(f"/api/step-plans/{plan_id}/launch")
    try:
        async with SessionLocal() as session:
            await runner.launch(session, plan_id)
    except HTTPException as exc:
        return {"error": _http_detail(exc)}
    await publish_step_plan_changed(plan_id)
    return None


def _plan_scope_error(plan: Any, topic_id: int | None, chat_id: int | None) -> str | None:
    """Refuse a plan that does not belong to the calling conversation."""
    if topic_id is None and chat_id is None:
        return None
    if (topic_id is not None and plan.source_topic_id == topic_id) or (
        chat_id is not None and plan.source_chat_id == chat_id
    ):
        return None
    return "This plan belongs to another conversation; only its own conversation can control it."


async def _source_policy(session: Any, topic_id: int | None, chat_id: int | None) -> str | None:
    """Resolve the source conversation's current autonomy."""
    from precursor.backend.models import Chat, Topic
    from precursor.backend.services.app_settings import (
        resolve_chat_approval_policy,
        resolve_topic_approval_policy,
    )

    if topic_id is not None:
        topic = await session.get(Topic, topic_id)
        if topic is not None:
            return topic.approval_policy or await resolve_topic_approval_policy(session)
    if chat_id is not None:
        chat = await session.get(Chat, chat_id)
        if chat is not None:
            return chat.approval_policy or await resolve_chat_approval_policy(session)
    return None


async def _with_original_request(
    session: AsyncSession,
    delegated: str,
    *,
    topic_id: int | None,
    chat_id: int | None,
    source_message_id: int | None,
) -> str:
    if source_message_id is None:
        return delegated
    from fastapi import HTTPException, status

    message = await session.get(Message, source_message_id)
    if (
        message is None
        or message.role != MessageRole.USER
        or (topic_id is None) == (chat_id is None)
        or (topic_id is not None and message.topic_id != topic_id)
        or (chat_id is not None and message.chat_id != chat_id)
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid source user message.")
    original = message.content
    if not original.strip() or original in delegated:
        return delegated
    return (
        f"Original conversation request:\n{original}\n\n"
        "Delegated scope chosen for this plan (do not add work outside this scope; "
        f"retain the original request's relevant constraints):\n{delegated.strip()}"
    )


@mcp.tool()
async def create_step_plan(
    ask: str,
    topic_id: int | None = None,
    title: str | None = None,
    run: bool = False,
    chat_id: int | None = None,
    source_message_id: int | None = None,
) -> dict[str, Any]:
    """Delegate a long, multi-part job to *Step by step* instead of answering in one reply.

    Use this for substantial multi-stage work that benefits from separate
    drafting and review, such as several nontrivial deliverables that must agree
    on shared timings, identifiers, calculations or other constraints. A short,
    straightforward response does not need a plan just because it has several
    small parts; honor an explicit request not to delegate. A skill or a
    tool-built file can also benefit from a plan. When delegating such work,
    name the skill in ``ask`` so the plan's step can use it and file the result.

    Preserve the user's complete original request and constraints in ``ask``;
    do not omit an explicit step count, output format, or restriction on files
    or external lookups. Add any necessary delegated scope without changing it.
    The ask is split into a tree of steps (proposed by the model). With
    ``run=False`` (default) the plan waits for the user to review the steps and
    press Start. With ``run=True`` the steps are accepted and an automated run
    drafts and scores them one at a time, then attempts a final output in the
    background, subject to the source conversation's autonomy. The run can pause
    for approval or review before the final is filed. Pass exactly the current
    ``topic_id`` or ``chat_id`` so the plan shows up in that discussion and its
    final output can be filed back. The app supplies the current conversation
    when both are omitted.
    ``source_message_id`` identifies the originating user turn when known;
    the app supplies it automatically for the current conversation.

    Who reviews follows the topic's autonomy (pass ``topic_id``): *autonomous*,
    nobody; when the run ends the drafts are validated, the output is filed in
    the topic and you are asked to review it. *balanced*, the drafts are
    validated and the user does one final review of the output. *manual*, the
    user validates every step. *plan*, nothing runs: the plan is only drafted
    (``run`` is ignored) and the user starts it, then does one final review. For the last two you are woken in the topic once
    the user files the output, and then carry on by yourself.

    In the linked conversation, report the returned ``url`` and ``title`` once
    and end the turn instead of repeatedly checking or waiting for the run,
    unless the user asks you to wait. The card tracks progress and the app handles
    review and hand-back. Use ``get_step_plan`` to inspect a plan or answer a
    later progress question, and ``edit_step_plan`` when changes are requested.
    While a run is active, validated step drafts and their scores are not the
    assessed final output. Do not copy them into the source with ``append_note``
    or claim that they have been filed as the final. In autonomous mode the app
    files the assessed final and wakes the source only if checks pass.
    """
    if not await _section_enabled("step_plans"):
        return {"error": _GATED.format(section="step_plans")}
    if not ask.strip():
        return {"error": "ask must not be empty"}
    from fastapi import HTTPException

    from precursor.backend.schemas.step_plan import StepPlanCreate
    from precursor.backend.services import step_plan_llm as llm
    from precursor.backend.services import step_plans as svc
    from precursor.backend.services.events import publish_step_plan_changed

    try:
        async with SessionLocal() as session:
            policy = await _source_policy(session, topic_id, chat_id)
            if policy == "plan":
                run = False
            source_text = await _with_original_request(
                session,
                ask,
                topic_id=topic_id,
                chat_id=chat_id,
                source_message_id=source_message_id,
            )
            plan = await svc.create_plan(
                session,
                StepPlanCreate(
                    title=title if title and title.strip() else svc.title_from(ask),
                    topic_id=topic_id,
                    chat_id=chat_id,
                    source_text=source_text,
                    propose=False,
                    automation_mode="assisted",
                    delegation_policy=policy,
                ),
            )
            plan_id = plan.id
    except HTTPException as exc:
        return {"error": _http_detail(exc)}
    except ValidationError as exc:
        return {"error": f"Invalid plan request: {exc}"}
    await publish_step_plan_changed(plan_id)
    warning: str | None = None
    try:
        async with SessionLocal() as session:
            await llm.suggest_steps(session, plan_id, mode="initial")
    except HTTPException as exc:
        warning = f"The plan was created but the steps could not be proposed: {_http_detail(exc)}"
    await publish_step_plan_changed(plan_id)
    if run and warning is None:
        failure = await _launch_run(plan_id)
        if failure is not None:
            warning = failure["error"]
    summary = await _plan_summary(plan_id)
    if policy == "plan":
        summary["next"] = (
            "This conversation is in Plan mode: the plan is only drafted. Do not start it. Tell the "
            "user to review the steps and press Start the run on the plan; amend the steps "
            "with edit_step_plan if they ask."
        )
    if run:
        summary["run_started"] = warning is None
    if warning:
        summary["warning"] = warning
        summary["next"] = (
            "Do not do the plan's work yourself. Tell the user what failed, then retry "
            "run_step_plan or let them press Run in Step by step."
        )
    return summary


@mcp.tool()
async def list_step_plans(
    topic_id: int | None = None, chat_id: int | None = None
) -> dict[str, Any]:
    """List plans, optionally filtered to one source topic or chat."""
    if not await _section_enabled("step_plans"):
        return {"error": _GATED.format(section="step_plans")}
    from precursor.backend.services import step_plans as svc

    async with SessionLocal() as session:
        items = await svc.list_plans(session, topic_id, chat_id)
    return {
        "plans": [
            {
                "plan_id": p.id,
                "title": p.title,
                "status": p.status,
                "topic_id": p.source_topic_id,
                "chat_id": p.source_chat_id,
                "steps": p.step_count,
                "validated": p.validated_count,
                "url": _plan_url(p.id),
            }
            for p in items
        ]
    }


@mcp.tool()
async def get_step_plan(
    plan_id: int, wait_seconds: int = 0, include_artifacts: bool = False
) -> dict[str, Any]:
    """Read a plan: its status, run state, progress and the tree of steps.

    ``run_state`` is ``running`` while the automated run is alive. Set
    ``wait_seconds`` (max 120) to block until the run stops or the delay
    elapses, instead of polling. ``include_artifacts`` adds each step's draft
    and the final output text (can be large). Steps have a ``status`` of
    ``open``, ``to_validate`` (a draft waits for the user), ``validated``,
    ``cancelled`` (skipped) or ``proposed``. A paused run is not complete: use
    its activity to explain the blocker, and do not bypass a held draft by
    assembling a final answer yourself. A running plan is not finished merely
    because every step is validated: its final review and hand-back still need
    to happen. In the linked conversation, end the turn instead of checking the
    run repeatedly unless the user asked you to wait; the app handles hand-back.
    """
    if not await _section_enabled("step_plans"):
        return {"error": _GATED.format(section="step_plans")}
    import asyncio

    from fastapi import HTTPException

    deadline = min(max(wait_seconds, 0), _MAX_PLAN_WAIT_SECONDS)
    waited = 0
    try:
        while True:
            summary = await _plan_summary(plan_id, include_artifacts=include_artifacts)
            if summary["run_state"] != "running" or waited >= deadline:
                return summary
            await asyncio.sleep(2)
            waited += 2
    except HTTPException as exc:
        return {"error": _http_detail(exc)}


@mcp.tool()
async def amend_step_plan(
    plan_id: int,
    request: str,
    steps: list[dict[str, Any]],
    expected_state_token: str,
    topic_id: int | None = None,
    chat_id: int | None = None,
    output_instructions: str | None = None,
    clear_output_instructions: bool = False,
    run: bool = True,
    source_message_id: int | None = None,
) -> dict[str, Any]:
    """Extend a related plan atomically with the user's follow-up and only the missing steps.

    First use ``list_step_plans`` for the current conversation and ``get_step_plan``
    with ``include_artifacts=True`` to understand its existing work. Pass the fresh
    ``state_token`` as ``expected_state_token``, and exactly the matching ``topic_id``
    or ``chat_id``. The app supplies the current conversation when both are omitted.
    Each step needs a title and instruction; optional ``parent_step_id``
    and ``position`` follow the normal step-creation rules. Preserve the full follow-up,
    including explicit limits and restrictions, in ``request``. Add only genuinely new
    work and honor an explicit step count: the app already assembles the revised final,
    so do not add a separate assembly step. Existing steps and
    final versions are retained and cannot be edited by an amendment: to change a
    delivered section, add a step that produces its revised version and say in
    its instruction which section it supersedes. The final output becomes stale and is rebuilt
    from the revised requirements.

    The follow-up in ``request`` already changes the final requirements. Omit
    ``output_instructions`` unless the separate output instructions need to
    change; if they do, provide their complete replacement, or set
    ``clear_output_instructions`` to remove them. Do not introduce exceptions
    to retained user constraints or describe an unwanted title as allowed.
    With ``run=True`` the plan resumes according to the source
    conversation's current autonomy; in Plan mode only the user can start it.
    A running plan cannot be amended until its current run has stopped.
    Once resumed, let the app handle review and filing as for a new plan.
    In the linked conversation, report the amendment once and end the turn
    instead of repeatedly checking the run, unless the user asks you to wait.
    Do not post a step draft as the revised final.
    ``source_message_id`` identifies the originating user turn when known;
    the app supplies it automatically for the current conversation.
    """
    if not await _section_enabled("step_plans"):
        return {"error": _GATED.format(section="step_plans")}
    if (topic_id is None) == (chat_id is None):
        return {"error": "Pass exactly one matching topic_id or chat_id."}
    if clear_output_instructions and output_instructions is not None:
        return {"error": "Choose replacement output instructions or clear them, not both."}
    from fastapi import HTTPException
    from pydantic import ValidationError

    from precursor.backend.schemas.step_plan import StepPlanAmendRequest
    from precursor.backend.services import step_plans as svc
    from precursor.backend.services.events import is_app_process, publish_step_plan_changed

    data: dict[str, Any] = {
        "request": request,
        "steps": steps,
        "expected_state_token": expected_state_token,
        "topic_id": topic_id,
        "chat_id": chat_id,
    }
    if clear_output_instructions:
        data["output_instructions"] = None
    elif output_instructions is not None:
        data["output_instructions"] = output_instructions
    try:
        async with SessionLocal() as session:
            data["request"] = await _with_original_request(
                session,
                request,
                topic_id=topic_id,
                chat_id=chat_id,
                source_message_id=source_message_id,
            )
        payload = StepPlanAmendRequest.model_validate(data)
        if is_app_process():
            async with SessionLocal() as session:
                await svc.amend_plan(session, plan_id, payload)
            await publish_step_plan_changed(plan_id)
        else:
            failure = await _app_post(
                f"/api/step-plans/{plan_id}/amend",
                payload.model_dump(mode="json", exclude_unset=True),
            )
            if failure is not None:
                return failure
        summary = await _plan_summary(plan_id)
    except HTTPException as exc:
        return {"error": _http_detail(exc)}
    except ValidationError as exc:
        return {"error": f"Invalid amendment: {exc}"}

    summary["amended"] = True
    if run and summary["delegation_policy"] == "plan":
        summary["run_started"] = False
        summary["next"] = (
            "The amendment was saved in Plan mode. Do not start it; the user reviews "
            "the changes and presses Start the run."
        )
    elif run:
        failure = await _launch_run(plan_id)
        if failure is not None:
            summary["run_started"] = False
            summary["warning"] = (
                "The amendment was saved, but its run could not start: " + failure["error"]
            )
        else:
            summary = await _plan_summary(plan_id)
            summary["amended"] = True
            summary["run_started"] = True
    return summary


@mcp.tool()
async def edit_step_plan(plan_id: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Change a plan's steps. Each operation is an object with an ``op`` key:

    - ``{"op": "add", "title": …, "instruction": …, "parent_step_id": …?}``
    - ``{"op": "update", "step_id": …, "title": …?, "instruction": …?}``
    - ``{"op": "delete", "step_id": …}``
    - ``{"op": "skip", "step_id": …}`` (leave a step out of the final output)
    - ``{"op": "accept_all"}`` (turn every suggested step into real work)

    Step ids come from ``get_step_plan``. Operations apply in order and stop at
    the first failure. Approving a step's result is not available: it stays
    with the user.
    """
    if not await _section_enabled("step_plans"):
        return {"error": _GATED.format(section="step_plans")}
    from fastapi import HTTPException
    from pydantic import ValidationError

    from precursor.backend.schemas.step_plan import StepCreate, StepUpdate
    from precursor.backend.services import step_plans as svc
    from precursor.backend.services.events import publish_step_plan_changed

    applied = 0
    try:
        for op in operations:
            kind = op.get("op")
            async with SessionLocal() as session:
                plan = await svc.get_plan(session, plan_id)
                svc.require_unarchived(plan)
                if kind == "add":
                    await svc.create_step(
                        session,
                        plan,
                        StepCreate(
                            title=op.get("title", ""),
                            instruction=op.get("instruction", ""),
                            parent_step_id=op.get("parent_step_id"),
                        ),
                        author="model",
                    )
                elif kind in ("update", "delete", "skip"):
                    step = await svc.get_step(session, int(op.get("step_id", 0)))
                    if step.plan_id != plan_id:
                        return {"error": f"Step {step.id} is not part of plan {plan_id}"}
                    if kind == "update":
                        await svc.update_step(
                            session,
                            step.id,
                            StepUpdate(title=op.get("title"), instruction=op.get("instruction")),
                        )
                    elif kind == "delete":
                        await svc.delete_step(session, step.id)
                    else:
                        await svc.set_step_status(session, step.id, "cancelled")
                elif kind == "accept_all":
                    await svc.accept_all_proposed(session, plan)
                    await session.commit()
                else:
                    return {"error": f"Unknown operation {kind!r} (after {applied} applied)"}
            applied += 1
    except HTTPException as exc:
        return {"error": f"{_http_detail(exc)} (after {applied} applied)"}
    except (ValidationError, ValueError, TypeError) as exc:
        return {"error": f"Invalid operation: {exc} (after {applied} applied)"}
    await publish_step_plan_changed(plan_id)
    return await _plan_summary(plan_id)


@mcp.tool()
async def run_step_plan(
    plan_id: int, topic_id: int | None = None, chat_id: int | None = None
) -> dict[str, Any]:
    """Start (or resume) the automated run of a plan.

    ``topic_id`` or ``chat_id`` is supplied by the app for the current conversation,
    which can only run its own plans.

    Accepts the suggested steps if the plan has not started, switches it to
    fully automated, then drafts and scores each step with no artifact one at a
    time and attempts a final output in the background. Source autonomy decides
    whether checked drafts are automatically validated and the final is filed;
    a standalone manual-policy run leaves approval to the user. The run can pause
    for tool approval or review. In the linked conversation, report the run and end the
    turn instead of repeatedly checking it, unless the user asks you to wait.
    Use ``get_step_plan`` when progress is needed.
    """
    if not await _section_enabled("step_plans"):
        return {"error": _GATED.format(section="step_plans")}
    from fastapi import HTTPException

    from precursor.backend.services import step_plans as svc

    try:
        async with SessionLocal() as session:
            plan = await svc.get_plan(session, plan_id)
            if (mismatch := _plan_scope_error(plan, topic_id, chat_id)) is not None:
                return {"error": mismatch}
            policy = await _source_policy(session, plan.source_topic_id, plan.source_chat_id)
            if policy is not None and policy != plan.delegation_policy:
                plan.delegation_policy = policy
                await session.commit()
            if plan.delegation_policy == "plan":
                return {
                    "error": "This plan was made in Plan mode: only the user can start it, "
                    "from the plan card or Step by step."
                }
    except HTTPException as exc:
        return {"error": _http_detail(exc)}
    failure = await _launch_run(plan_id)
    if failure is not None:
        return failure
    try:
        return await _plan_summary(plan_id)
    except HTTPException as exc:
        return {"error": _http_detail(exc)}


@mcp.tool()
async def pause_step_plan(
    plan_id: int, topic_id: int | None = None, chat_id: int | None = None
) -> dict[str, Any]:
    """Pause a plan's automated run once the step in flight is done.

    ``topic_id`` or ``chat_id`` is supplied by the app for the current conversation,
    which can only pause its own plans.
    """
    if not await _section_enabled("step_plans"):
        return {"error": _GATED.format(section="step_plans")}
    from fastapi import HTTPException

    from precursor.backend.services import step_plan_runner as runner
    from precursor.backend.services import step_plans as svc
    from precursor.backend.services.events import is_app_process, publish_step_plan_changed

    try:
        if topic_id is not None or chat_id is not None:
            async with SessionLocal() as session:
                scoped = await svc.get_plan(session, plan_id)
                if (mismatch := _plan_scope_error(scoped, topic_id, chat_id)) is not None:
                    return {"error": mismatch}
        if is_app_process():
            async with SessionLocal() as session:
                await runner.pause(session, plan_id)
        else:
            failure = await _app_post(f"/api/step-plans/{plan_id}/auto/pause")
            if failure is not None:
                return failure
        await publish_step_plan_changed(plan_id)
        return await _plan_summary(plan_id)
    except HTTPException as exc:
        return {"error": _http_detail(exc)}
