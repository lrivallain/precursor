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
from precursor.backend.services.app_settings import (
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_reasoning_effort,
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
        await apply_agent_model(
            agent, live, default_model=default_model, effort=effort, tier=tier, pinned=run.model
        )

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
            await apply_agent_model(
                agent,
                live,
                default_model=default_model,
                effort=effort,
                tier=tier,
                pinned=run.model,
            )


async def apply_agent_model(
    agent: AgentSession,
    live: _LiveSession,
    *,
    default_model: str,
    effort: str,
    tier: str,
    pinned: str | None = None,
) -> None:
    """``set_model`` a single idle live agent to its selected model.

    The model is ``pinned or agent.model or default_model`` — the executing
    run's snapshot wins (a workflow step can pin a model for its turn), then
    an explicit per-agent pin, otherwise the current composer/Settings
    selection applies. History preserving and effective on the agent's next
    turn. No-op when the target (model, effort, tier) already matches what we
    last applied.
    """
    model = pinned or agent.model or default_model
    if not model:
        return
    signature = (model, effort or None, tier or "default")
    if live.model_signature == signature:
        return
    # Always send the tier (incl. "default") so toggling back resets it;
    # a falsy effort is sent as None so the runtime restores the model
    # default rather than pinning a stale level.
    kwargs: dict[str, Any] = {"context_tier": tier or "default"}
    if effort:
        kwargs["reasoning_effort"] = effort
    try:
        await live.sdk_session.set_model(model, **kwargs)
        live.model_signature = signature
    except Exception:
        logger.debug("set_model failed for agent %s", agent.id, exc_info=True)
