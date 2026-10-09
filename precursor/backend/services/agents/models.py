"""Model selection for agent SDK sessions.

Lists the runtime's model catalogue, guards against a persisted model the
runtime no longer offers, and reconciles live sessions to the current
model / reasoning-effort / context-tier selection. ``AgentManager`` delegates
here; this module must not import ``manager`` at runtime.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentRun, AgentSession
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.schemas.model_fallback import ModelCategory
from precursor.backend.services.app_settings import (
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_reasoning_effort,
)
from precursor.backend.services.llm.base import LLMError
from precursor.backend.services.model_fallbacks import (
    ModelSelection,
    category_presets,
    is_model_rejection,
    resolve_model_category,
    resolve_model_fallbacks,
    selected_category_presets,
)

if TYPE_CHECKING:
    from precursor.backend.services.agents.live_session import _LiveSession
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)


class ModelSelector:
    def __init__(self, manager: AgentManager) -> None:
        # Held as a back-reference and read at call time, never captured as
        # bound methods, so a test patching the manager still takes effect.
        self._manager = manager

    async def list_models(self) -> list[dict[str, Any]]:
        """Return the runtime's available models, or empty.

        Used to populate the model picker. Surfaces each model's context window
        and advertised reasoning-effort set so the composer can adapt its
        controls. The SDK caches the result after the first call.
        """
        if not self._manager._ready or self._manager._client is None:
            return []
        try:
            models = await self._manager._client.list_models()
        except Exception:
            logger.debug("list_models failed", exc_info=True)
            return []
        out: list[dict[str, Any]] = []
        for m in models or []:
            mid = getattr(m, "id", None)
            if not mid:
                continue
            caps = getattr(m, "capabilities", None)
            limits = getattr(caps, "limits", None) if caps is not None else None
            ctx = None
            if limits is not None:
                ctx = getattr(limits, "max_prompt_tokens", None) or getattr(
                    limits, "max_context_window_tokens", None
                )
            efforts = getattr(m, "supported_reasoning_efforts", None) or []
            out.append(
                {
                    "id": str(mid),
                    "name": str(getattr(m, "name", None) or mid),
                    "context_window": int(ctx) if isinstance(ctx, (int, float)) else None,
                    "supported_reasoning_efforts": [str(e) for e in efforts],
                }
            )
        return out

    async def available_model_ids(self) -> set[str]:
        """Model ids the runtime currently offers (empty when unavailable).

        Guards against a stale persisted default: the SDK's catalogue rotates
        over time, so a model that was valid when it was saved can vanish, and
        passing a now-unknown id to ``create_session`` fails the whole turn.
        """
        return {m["id"] for m in await self._manager.list_models()}

    async def sanitize(self, agent_id: int, model: str) -> str:
        """Return ``model`` if the runtime still offers it, else ``"auto"``.

        Only downgrades when we actually have a catalogue to check against: an
        empty catalogue (runtime momentarily down) leaves the selection intact
        so we never mask a transient failure as a model change. ``"auto"`` is
        always accepted, so it's the safe fallback for a vanished pin.
        """
        if not model or model == "auto":
            return model
        available = await self.available_model_ids()
        if available and model not in available:
            logger.warning(
                "agent %s: model %r is no longer offered by the runtime — falling back to 'auto'",
                agent_id,
                model,
            )
            return "auto"
        return model

    async def choices(
        self,
        agent_id: int,
        model: str,
        effort: str,
        tier: str,
        *,
        category: ModelCategory | None = None,
    ) -> list[tuple[str, str | None, str]]:
        initial = (model, effort or None, tier or "default")
        if category is None and (not model or model == "auto"):
            return [initial]
        async with SessionLocal() as session:
            categories = await resolve_model_fallbacks(session, "agents")
        selection = ModelSelection(model=model, reasoning_effort=effort, context_tier=tier)
        presets = (
            selected_category_presets(categories, category)
            if category is not None
            else category_presets(categories, selection, agents=True)
        )
        if category is not None and not presets:
            raise LLMError(
                f"No presets are configured for the selected category {category.value!r}. "
                "Update Settings > Model > Manage presets."
            )
        if not presets:
            return [(await self.sanitize(agent_id, model), effort or None, tier or "default")]
        available = await self.available_model_ids()
        choices = list(
            dict.fromkeys(
                [
                    *([initial] if category is None else []),
                    *((p.model, p.reasoning_effort or None, p.context_tier) for p in presets),
                ]
            )
        )
        choices = [c for c in choices if not available or c[0] in available]
        if not choices:
            target = category.value if category is not None else model
            raise LLMError(
                f"No available model remains in the category for {target!r}. "
                "Update Settings > Model > Model alternatives."
            )
        return choices

    async def _apply(
        self,
        agent: AgentSession,
        run: AgentRun,
        live: _LiveSession,
        default_model: str,
        effort: str,
        tier: str,
        category: ModelCategory | None,
    ) -> None:
        model = run.model or agent.model or default_model
        category = category if not (run.model or agent.model) else None
        requested = (model, effort or None, tier or "default")
        choices = await self.choices(agent.id, model, effort, tier, category=category)
        if (
            live.requested_model_signature == requested
            and live.requested_model_category == category
            and live.model_candidates == choices
            and live.model_signature in choices
        ):
            return
        live.model_candidates = choices
        live.model_attempts.clear()
        for choice in choices:
            live.model_attempts.add(choice)
            try:
                await self._set_model(live, choice)
                live.requested_model_signature = requested
                live.requested_model_category = category
                if choice != requested:
                    await self.announce(run.id, choice, category=category)
                return
            except Exception as exc:
                if not is_model_rejection(exc):
                    raise
                logger.warning("agent %s: model configuration rejected: %s", agent.id, exc)
                if choice == choices[-1]:
                    raise

    @staticmethod
    async def _set_model(live: _LiveSession, choice: tuple[str, str | None, str]) -> None:
        model, effort, tier = choice
        # None explicitly clears the previous model's effort on a switch.
        await live.sdk_session.set_model(model, reasoning_effort=effort, context_tier=tier)
        live.model_signature = choice

    async def send(self, run_id: int, live: _LiveSession, prompt: str) -> None:
        live.model_turn_id += 1
        live.dispatched_prompt = prompt
        live.model_output_started = False
        live.model_recovering = False
        live.model_retry_scheduled = False
        if live.model_signature is not None:
            live.model_attempts.add(live.model_signature)
        while True:
            try:
                await live.sdk_session.send(prompt)
                return
            except Exception as exc:
                if not await self.replace_rejected(run_id, live, exc):
                    raise

    async def replace_rejected(
        self, run_id: int, live: _LiveSession, error: BaseException | str
    ) -> bool:
        if live.model_output_started or not is_model_rejection(error):
            return False
        for choice in live.model_candidates:
            if choice in live.model_attempts:
                continue
            live.model_attempts.add(choice)
            try:
                await self._set_model(live, choice)
            except Exception as exc:
                if not is_model_rejection(exc):
                    raise
                logger.warning("agent run %s: alternative rejected: %s", run_id, exc)
                continue
            logger.warning("agent run %s: using alternative %s after %s", run_id, choice, error)
            await self.announce(run_id, choice)
            return True
        return False

    async def announce(
        self,
        run_id: int,
        choice: tuple[str, str | None, str],
        *,
        category: ModelCategory | None = None,
    ) -> None:
        loaded = await self._manager._load_run(run_id)
        if loaded is not None:
            await self._manager._emit_synthetic(
                loaded[1].id,
                AgentEvent(
                    kind="model_selected" if category is not None else "model_fallback",
                    text=(
                        f"Using model {choice[0]} "
                        f"(effort: {choice[1] or 'auto'}, context: {choice[2]}). "
                        "The saved model selection is unchanged."
                    ),
                    agent_run_id=run_id,
                ),
            )

    async def recover(
        self, run_id: int, live: _LiveSession, error: str, *, turn_id: int | None = None
    ) -> None:
        expected_turn = live.model_turn_id if turn_id is None else turn_id
        try:
            run = await self._manager._run(run_id)
            if (
                run is None
                or run.status != "running"
                or self._manager._live.get(run_id) is not live
                or live.model_turn_id != expected_turn
            ):
                if live.model_turn_id == expected_turn:
                    live.model_recovering = False
                return
            while await self.replace_rejected(run_id, live, error):
                if not live.dispatched_prompt:
                    break
                run = await self._manager._run(run_id)
                if run is None or run.status != "running" or live.model_turn_id != expected_turn:
                    if live.model_turn_id == expected_turn:
                        live.model_recovering = False
                    return
                try:
                    await live.sdk_session.send(live.dispatched_prompt)
                    # A trailing idle from the rejected attempt must not finish
                    # the run; turn-start/content releases this guard.
                    return
                except Exception as exc:
                    if not is_model_rejection(exc):
                        raise
                    error = str(exc)
            raise LLMError(error)
        except Exception as exc:
            if live.model_turn_id != expected_turn:
                logger.warning(
                    "agent run %s: discarded stale model recovery failure: %s", run_id, exc
                )
                return
            live.model_recovering = False
            await self._manager._fail_turn(run_id, exc)
        finally:
            if live.model_turn_id == expected_turn:
                live.model_retry_scheduled = False

    async def sync_selected_model(self, agent: AgentSession, run: AgentRun) -> None:
        """Reconcile ``run``'s live session to the current model selection.

        Called right before a turn is dispatched so every next turn follows the
        composer/Settings selection, even on a long-lived reused session. Skipped
        when there's no live session yet (a fresh build already bakes in the
        selection).
        """
        live = self._manager._live.get(run.id)
        if live is None:
            return
        async with SessionLocal() as s:
            default_model = await resolve_agents_default_model(s)
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
            category = await resolve_model_category(s, agents=True)
        await self._apply(agent, run, live, default_model, effort, tier, category)

    async def apply_session_overrides(self) -> None:
        """Apply the current global model / reasoning-effort / context-tier prefs
        onto idle live sessions.

        Lets a change in the composer (or Settings → Agents) take effect on the
        next message of an in-progress conversation instead of only new sessions.
        Uses the SDK's ``set_model`` — history-preserving, effective next turn.
        Skips sessions with a turn in flight, where switching the model is unsafe;
        those pick the change up on their next idle dispatch.
        """
        if not self._manager._ready:
            return
        run_ids = list(self._manager._live.keys())
        if not run_ids:
            return
        async with SessionLocal() as s:
            default_model = await resolve_agents_default_model(s)
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
            category = await resolve_model_category(s, agents=True)
            runs = (
                (await s.execute(select(AgentRun).where(AgentRun.id.in_(run_ids)))).scalars().all()
            )
            agent_ids = {r.agent_id for r in runs}
            rows = (
                (await s.execute(select(AgentSession).where(AgentSession.id.in_(agent_ids))))
                .scalars()
                .all()
                if agent_ids
                else []
            )
        by_id = {a.id: a for a in rows}
        by_run = {r.id: r for r in runs}
        for run_id in run_ids:
            live = self._manager._live.get(run_id)
            run = by_run.get(run_id)
            agent = by_id.get(run.agent_id) if run is not None else None
            if live is None or run is None or agent is None:
                continue
            if run.status in {"running", "needs_approval", "pending"}:
                continue
            await self._apply(agent, run, live, default_model, effort, tier, category)
