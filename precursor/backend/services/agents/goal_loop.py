"""The autonomy goal loop: decide what a finished turn means for the agent's
mission, take the next autonomous step, and post the exchange back to the
linked topic or chat.

``AgentManager`` delegates here; this module must not import ``manager`` at
runtime.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentRun,
    AgentSession,
    Chat,
    Message,
    MessageRole,
    Topic,
)
from precursor.backend.services.agents.directives import (
    _CONTINUE_NUDGE,
    RESULT_SUMMARY_CAP,
    parse_agent_directives,
    strip_control_directives,
)
from precursor.backend.services.definitions import overlay as definition_overlay
from precursor.backend.services.definitions import trust as definition_trust
from precursor.backend.services.events import (
    publish_message_changed,
    publish_message_changed_chat,
)
from precursor.backend.services.suggestions import split_suggestions

if TYPE_CHECKING:
    from precursor.backend.services.agents.live_session import _LiveSession
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# --- Autonomy goal loop --------------------------------------------------------
# After this many consecutive no-progress continuation steps, the loop stops and
# parks the agent as ``blocked`` so a human can course-correct instead of letting
# it spin. Kept small — autonomy is about steady progress, not infinite retries.
_STALL_LIMIT = 3


class GoalLoop:
    def __init__(self, manager: AgentManager) -> None:
        # A back-reference read at call time (never captured bound methods), so a
        # test patching the manager still takes effect.
        self._manager = manager

    async def on_idle(self, agent: AgentSession, run: AgentRun, patch: dict[str, Any]) -> None:
        """Resolve what a finished turn means for an agent's mission.

        This is the goal loop. For a **plain** agent it just rests at ``idle`` and
        reposts the exchange (unchanged behaviour). For an **autonomous** agent it
        reads the control directives the model embedded in its last message and:

        * ``OBJECTIVE_COMPLETE`` → terminal ``completed`` (one final repost);
        * ``NEED_INPUT`` → ``blocked`` on the raised question (repost);
        * otherwise, if the step budget remains and it isn't stalling, it keeps
          going — status stays ``running``, the step count ticks up, and the next
          step is enqueued with **no** repost (so a whole multi-step mission lands
          as a single objective→result exchange when it finally rests);
        * budget exhausted or stalled → ``blocked`` for a human to course-correct.

        Progress (``PROGRESS: n | label``) is applied whenever present and resets
        the stall counter on a fresh value. Mutates ``patch`` in place.

        A **workflow step** that isn't autonomous still reports progress and
        publishes artifacts: its kickoff tells it to, and the next steps read the
        blackboard. Only the terminal directives stay autonomy-only, so such a
        step rests at ``idle`` for the coordinator however it signs off.

        Step count and progress are read from ``run``: each execution pursues the
        objective on its own budget, so a sibling run can't spend this one's steps.
        """
        m = self._manager
        live = m._live.get(run.id)
        speaks_protocol = agent.autonomy_enabled or run.workflow_run_id is not None
        directives = (
            parse_agent_directives(live.pending_answer)
            if live is not None and speaks_protocol
            else {}
        )
        if not agent.autonomy_enabled:
            directives.pop("complete", None)
            directives.pop("blocked", None)
        if live is not None:
            live.directive = directives or None

        # A progress report applies regardless of the terminal decision below, and
        # a *new* value clears the stall counter (the agent is genuinely moving).
        progress = directives.get("progress")
        if progress is not None:
            patch["progress"] = progress["value"]
            patch["progress_label"] = progress["label"]
            if live is not None and progress["value"] != live.last_progress:
                live.last_progress = progress["value"]
                live.stall_count = 0

        # Published artifacts land on the blackboard regardless of the terminal
        # decision below, so mid-mission outputs are shared as soon as they're
        # emitted (not only at completion).
        artifacts = directives.get("artifacts")
        if artifacts:
            await m._persist_artifacts(run.id, artifacts, kind="output")

        # 1) Objective met — terminal. The single repost carries the summary.
        if directives.get("complete"):
            # The OBJECTIVE_COMPLETE reason is a *meta* description of the work
            # ("Told the user a joke"); the actual deliverable is the message's
            # prose — the joke itself. Prefer that prose as the displayed result,
            # falling back to the reason only when the final turn was
            # directives-only (a bare OBJECTIVE_COMPLETE with no body).
            reason = strip_control_directives(str(directives["complete"]))[:RESULT_SUMMARY_CAP]
            body = (
                strip_control_directives(str(live.pending_answer))[:RESULT_SUMMARY_CAP]
                if live is not None and live.pending_answer
                else ""
            )
            summary = body or reason
            patch["status"] = "completed"
            patch["result_summary"] = summary
            patch["active_prompt"] = None
            patch["progress"] = 100
            patch["step_count"] = 0
            patch["finished_at"] = datetime.now(UTC)
            if live is not None:
                live.stall_count = 0
            # Auto-capture the outcome as a result artifact so downstream agents
            # get the deliverable even when the model didn't emit an ARTIFACT line.
            if summary:
                await m._persist_artifacts(
                    run.id, [{"title": "Result", "content": summary}], kind="result"
                )
            await m._notify_back(agent, run)
            return

        # 2) Agent raised a decision only the human can make — park it.
        if directives.get("blocked"):
            patch["status"] = "blocked"
            patch["blocked_question"] = str(directives["blocked"])[:2000]
            patch["active_prompt"] = None
            await m._notify_back(agent, run)
            return

        # 3) Autonomous continuation — keep pursuing the objective if allowed.
        if agent.autonomy_enabled:
            await self._continue(agent, run, live, progress, patch)
            return

        # 4) Plain agent: rest at idle and repost this turn's exchange.
        patch["status"] = "idle"
        patch["active_prompt"] = None
        await m._notify_back(agent, run)
        # Idle is not sticky, so a trailing idle (a background sub-agent settling)
        # re-enters here: consume the answer so its artifacts publish only once.
        if live is not None:
            live.pending_answer = None

    async def _continue(
        self,
        agent: AgentSession,
        run: AgentRun,
        live: _LiveSession | None,
        progress: Any,
        patch: dict[str, Any],
    ) -> None:
        """Keep an autonomous agent pursuing its objective, or park it as blocked."""
        m = self._manager
        # Files mode: autonomy, its step budget and the token budget are read
        # live from the file, so a widening that nobody accepted stops here.
        if definition_overlay.files_mode():
            async with SessionLocal() as review_session:
                changes = await definition_trust.pending_changes(review_session, agent)
            if changes:
                patch["status"] = "blocked"
                patch["blocked_question"] = definition_trust.review_message(agent.title, changes)
                patch["active_prompt"] = None
                await m._notify_back(agent, run)
                return
        if live is not None and live.stall_count >= _STALL_LIMIT:
            patch["status"] = "blocked"
            patch["blocked_question"] = (
                "I've taken several steps without measurable progress toward "
                "the objective. Please review and advise on how to proceed."
            )
            patch["active_prompt"] = None
            await m._notify_back(agent, run)
            return
        if run.step_count < agent.max_steps:
            # Keep going. No repost — the mission is still in flight; the
            # single objective→result exchange lands when it rests.
            patch["status"] = "running"
            patch["step_count"] = run.step_count + 1
            # No progress advance this step counts toward a stall.
            if live is not None and progress is None:
                live.stall_count += 1
            m.enqueue(self.advance(run.id))
            return
        # Budget spent without completing — hand back rather than run forever.
        patch["status"] = "blocked"
        patch["blocked_question"] = (
            f"I've reached the step budget ({agent.max_steps} steps) for this "
            "objective without completing it. Review my progress and tell me "
            "whether to continue, adjust the objective, or stop."
        )
        patch["active_prompt"] = None
        await m._notify_back(agent, run)

    async def advance(self, run_id: int) -> None:
        """Take the next autonomous step toward the objective.

        Enqueued (never called inline) from the idle handler — we must not
        ``send`` from inside the locked event handler. Reloads the run,
        re-checks it's still an autonomous execution that should continue,
        refreshes the per-turn approval policy, and nudges the SDK session to keep
        working. ``pending_prompt`` is deliberately left untouched so the eventual
        single repost still shows the objective + final answer.
        """
        m = self._manager
        agent_id: int | None = None
        try:
            loaded = await m._load_run(run_id)
            if loaded is None:
                return
            run, agent = loaded
            agent_id = agent.id
            if not agent.autonomy_enabled or run.status != "running":
                return
            live = m._live.get(run_id)
            if live is None:
                return
            live.approval_policy = await m._approval_policy(agent, run)
            # Reset the per-turn answer buffer so the next directive parse reads
            # only the upcoming step's message.
            live.pending_answer = None
            await m._model_selection.send(run_id, live, _CONTINUE_NUDGE)
        except Exception as exc:
            if agent_id is not None:
                await m._fail_turn(run_id, exc)

    async def notify_back(self, agent: AgentSession, run: AgentRun) -> None:
        """Post the just-finished turn's exchange into the linked container.

        Posts the turn's **prompt** (as a user turn) and the agent's **answer**
        (as an assistant turn), both tagged with ``agent_session_id`` so the UI
        renders an "agent exchange" badge linking back to ``/agents/{id}``. Like
        the reminder ticker, the discussion goes unread + notifies.

        Posts **once per turn**: the prompt is captured on ``_LiveSession`` when a
        task/follow-up is sent and cleared here, so repeated idle events for the
        same turn don't double-post and every turn (not just the first) lands in
        the topic. A resumed turn with no tracked prompt is skipped.
        """
        m = self._manager
        if agent.topic_id is None and agent.chat_id is None:
            return

        live = m._live.get(run.id)
        if live is None or live.pending_prompt is None:
            return
        prompt = live.pending_prompt
        live.pending_prompt = None

        # Prefer the full assistant text captured this turn; fall back to the
        # (capped) summary so a resumed turn without a tracked answer still posts.
        answer = (live.pending_answer or run.result_summary or "").strip() or "Agent task finished."
        live.pending_answer = None
        answer, suggestions = split_suggestions(answer)
        now = datetime.now(UTC)
        # Keep the posted messages strictly newer than any last_read_at we pin,
        # so the unread badge lights up reliably (mirrors the reminder ticker).
        read_threshold = now - timedelta(seconds=1)
        async with SessionLocal() as session:
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.USER,
                    content=prompt,
                    agent_session_id=agent.id,
                    created_at=now,
                )
            )
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.ASSISTANT,
                    content=answer,
                    suggestions=json.dumps(suggestions) if suggestions else None,
                    agent_session_id=agent.id,
                    created_at=now,
                )
            )
            # Ensure the linked conversation reads as unread even when it was
            # never opened: last_read_at IS NULL is treated as fully read, so the
            # agent's reply wouldn't count. Pin last_read just before the messages
            # when null (or somehow stamped in the future) without masking other
            # genuinely-unread history. Mirrors services/reminders.py.
            container: Topic | Chat | None = None
            if agent.topic_id is not None:
                container = await session.get(Topic, agent.topic_id)
            elif agent.chat_id is not None:
                container = await session.get(Chat, agent.chat_id)
            if container is not None:
                last_read = container.last_read_at
                if last_read is not None and last_read.tzinfo is None:
                    last_read = last_read.replace(tzinfo=UTC)
                if last_read is None or last_read > read_threshold:
                    container.last_read_at = read_threshold
            await session.commit()
        if agent.topic_id is not None:
            await publish_message_changed(agent.topic_id)
        elif agent.chat_id is not None:
            await publish_message_changed_chat(agent.chat_id)
