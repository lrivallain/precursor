"""Turn dispatch: starting an agent's task, sending it a message, resuming or
cancelling a turn, and recording a turn that could not be dispatched.

``AgentManager`` delegates here; this module must not import ``manager`` at
runtime.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentRun
from precursor.backend.services.definitions import overlay as definition_overlay
from precursor.backend.services.definitions import trust as definition_trust
from precursor.backend.services.events import publish_agent_changed

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)


class TurnDispatcher:
    def __init__(self, manager: AgentManager) -> None:
        # A back-reference read at call time (never captured bound methods), so a
        # test patching the manager still takes effect.
        self._manager = manager

    async def start_task(
        self,
        agent_id: int,
        extra_context: str | None = None,
        *,
        run_id: int | None = None,
        trigger: str = "manual",
    ) -> None:
        """Begin a fresh execution of the agent's objective.

        Fresh human intent is a *new execution*: unless the caller supplies a run
        (workflow steps open theirs in ``_launch_step`` so the step and the run
        are linked), a new :class:`AgentRun` is opened here. The step/stall/prompt
        resets below then fall out of starting on a new row rather than
        destroying whatever another workflow is running on the same agent.
        """
        m = self._manager
        run: AgentRun | None = None
        try:
            run = (
                await m._run(run_id)
                if run_id is not None
                else await m._open_run(agent_id, trigger=trigger)
            )
            if run is None:
                return
            agent = await m._load(agent_id)
            if agent is None:
                return
            # Files mode: a broken definition file must not quietly run the stale
            # database copy of the agent instead.
            if (problem := definition_overlay.definition_error(agent)) is not None:
                raise RuntimeError(f"Can't start '{agent.title}': {problem}")
            # ...nor start with permissions its file widened and nobody accepted.
            if definition_overlay.files_mode():
                async with SessionLocal() as review_session:
                    changes = await definition_trust.pending_changes(review_session, agent)
                if changes:
                    raise RuntimeError(definition_trust.review_message(agent.title, changes))
            # A fresh objective run starts from a clean blackboard: drop any
            # artifacts *this run* published so the new turn's deliverables
            # replace them rather than piling up beside stale ones. Scoped to the
            # run, so a concurrent workflow's blackboard is untouched.
            await m._clear_artifacts(run.id)
            live = await m._ensure_live(agent, run)
            # A step that named a server it couldn't sign in to must not run: the
            # agent would answer from its own knowledge and the workflow would
            # record that as a success. Park it so the run pauses on the sign-in.
            blocked_on = m._blocked_on_missing_auth(agent, live)
            if blocked_on:
                await m._block_turn(run.id, blocked_on)
                return
            await m._permissions.release_parked_turn(agent_id, live)
            await m._model_selection.sync_selected_model(agent, run)
            live.approval_policy = await m._approval_policy(agent, run)
            prompt = (agent.task_prompt or "").strip() or None
            live.pending_prompt = prompt
            live.pending_answer = None
            # Fresh human intent → reset the autonomy budget and stall tracking so
            # the objective run starts from a clean step count.
            live.stall_count = 0
            live.last_progress = None
            await m._patch_run(
                run.id,
                status="running",
                error=None,
                active_prompt=prompt,
                step_count=0,
                blocked_question=None,
            )
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=agent.topic_id,
                chat_id=agent.chat_id,
                agent_run_id=run.id,
            )
            # Fleet dependents receive their upstreams' published artifacts as a
            # kickoff preamble ahead of the durable objective, so results flow
            # down the DAG without a human relaying them.
            sent = agent.task_prompt
            if extra_context:
                sent = f"{extra_context}\n\n---\n\n{agent.task_prompt}"
            await m._model_selection.send(run.id, live, sent)
        except Exception as exc:
            if run is not None:
                await m._fail_turn(run.id, exc)

    async def send_message(self, agent_id: int, text: str) -> None:
        m = self._manager
        run: AgentRun | None = None
        try:
            agent = await m._load(agent_id)
            if agent is None:
                return
            run = await m._resolve_run(agent_id)
            if run is None:
                return
            live = await m._ensure_live(agent, run)
            await m._model_selection.sync_selected_model(agent, run)
            live.approval_policy = await m._approval_policy(agent, run)
            prompt = text.strip() or None
            live.pending_prompt = prompt
            live.pending_answer = None
            # A human message is fresh intent: reset the autonomy budget and, if
            # the agent had parked itself with a question, clear that block — this
            # message is the answer that unsticks it.
            live.stall_count = 0
            live.last_progress = None
            await m._patch_run(
                run.id,
                status="running",
                active_prompt=prompt,
                step_count=0,
                blocked_question=None,
            )
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=agent.topic_id,
                chat_id=agent.chat_id,
                agent_run_id=run.id,
            )
            await m._model_selection.send(run.id, live, text)
        except Exception as exc:
            if run is not None:
                await m._fail_turn(run.id, exc)

    async def resume(self, agent_id: int) -> None:
        """Re-run the in-flight turn of an interrupted session.

        Re-sends the persisted ``active_prompt`` (the turn cut off by a restart
        or the watchdog) so it finishes and notifies back. A no-op when there's
        nothing tracked to resume.
        """
        m = self._manager
        agent = await m._load(agent_id)
        if agent is None:
            return
        run = await m._resolve_run(agent_id)
        if run is None:
            return
        prompt = (run.active_prompt or "").strip()
        if not prompt:
            return
        try:
            live = await m._ensure_live(agent, run)
            await m._model_selection.sync_selected_model(agent, run)
            live.approval_policy = await m._approval_policy(agent, run)
            live.pending_prompt = prompt
            live.pending_answer = None
            await m._patch_run(run.id, status="running", error=None)
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=agent.topic_id,
                chat_id=agent.chat_id,
                agent_run_id=run.id,
            )
            await m._model_selection.send(run.id, live, prompt)
        except Exception as exc:
            await m._fail_turn(run.id, exc)

    async def cancel(self, agent_id: int, *, run_id: int | None = None) -> None:
        """Stop an execution. ``run_id`` targets a specific one — the watchdog
        needs that, because a shared agent's *current* run may belong to another
        workflow entirely."""
        m = self._manager
        run = await m._run(run_id) if run_id is not None else await m._resolve_run(agent_id)
        live = m._live.get(run.id) if run is not None else None
        if live is not None:
            with contextlib.suppress(Exception):
                await live.sdk_session.abort()
            for fut in live.pending.values():
                if not fut.done():
                    fut.set_result(m._reject("cancelled"))
        if run is not None:
            await m._patch_run(run.id, status="cancelled")
        await publish_agent_changed(agent_session_id=agent_id, agent_run_id=run.id if run else None)

    async def block_turn(self, run_id: int, labels: list[str]) -> None:
        """Park this run as ``blocked`` instead of dispatching a tool-less turn.

        Like :meth:`_fail_turn` this lands *outside* the SDK event seam — no turn
        is ever sent, so nothing will emit an event — hence the explicit workflow
        advance. ``blocked`` is what pauses the run (rather than letting the step
        complete on an ``idle`` the agent reached without ever calling a tool),
        and the question tells the user exactly which sign-in unblocks it.

        Scoped to the run: a shared agent's *other* execution may hold a valid
        session for the very server this one is missing, and parking the agent
        would strand it.
        """
        m = self._manager
        joined = ", ".join(labels)
        question = (
            f"This step needs {joined}, but the sign-in has expired. Re-authenticate, then resume."
        )
        logger.info("agent run %s: blocking turn — no credentials for %s", run_id, joined)
        await m._patch_run(run_id, status="blocked", blocked_question=question, error=None)
        loaded = await m._load_run(run_id)
        if loaded is not None:
            await m._publish(loaded[1].id, agent_run_id=run_id)
        m.enqueue(m._advance_workflows(run_id))

    async def fail_turn(self, run_id: int, exc: BaseException) -> None:
        """Mark a turn as failed so a dispatch error is visible, not a silent hang.

        Turn dispatch runs as a detached background task (:meth:`enqueue`), so an
        exception there would otherwise be swallowed and leave the agent stuck on
        its "sending…" spinner. Record it as a ``failed`` status with the error
        text and publish, mirroring how the SDK's own ``ErrorData`` is surfaced.

        This lands *outside* the event seam, so the workflow advance is enqueued
        here explicitly: a step whose dispatch blew up emits no further events,
        and without this its run would sit in ``running`` until the watchdog (if
        one is configured at all) noticed.
        """
        m = self._manager
        logger.exception("agent run %s: turn dispatch failed", run_id)
        message = str(exc).strip() or exc.__class__.__name__
        with contextlib.suppress(Exception):
            await m._patch_run(run_id, status="failed", error=message[:2000])
            loaded = await m._load_run(run_id)
            if loaded is not None:
                await m._publish(loaded[1].id, agent_run_id=run_id)
            m.enqueue(m._advance_workflows(run_id))
