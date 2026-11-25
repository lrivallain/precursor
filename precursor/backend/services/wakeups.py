"""Self wakeups: a model pauses its conversation and wakes it up later.

A model waiting on something outside the conversation (a CI run, a deploy, a
long job) calls ``schedule_wakeup`` and ends its turn. The conversation is then
"paused" until ``wake_at``, when the ticker (``services/wakeup_ticker``) sends
the model's own prompt back in as a new turn. Topics and chats get the tools as
built-in tools of the turn (``services/wakeup_tools``); agents get them from the
Precursor MCP server, keyed by their id.

Everything here is keyed by a ``(kind, target_id)`` pair, kind being
``"topic"``, ``"chat"`` or ``"agent"``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from precursor.backend.clock import utcnow
from precursor.backend.models import AgentSession, Chat, Topic, Wakeup
from precursor.backend.schemas.wakeup import WakeupKind, WakeupRead
from precursor.backend.services.events import publish_wakeup_changed

#: ``Message.kind`` of the user turn a wakeup sends.
MESSAGE_KIND_WAKEUP = "wakeup"
MIN_DELAY = timedelta(seconds=10)
MAX_DELAY = timedelta(days=30)
MAX_PROMPT_CHARS = 8000

_MODELS: dict[WakeupKind, Any] = {"topic": Topic, "chat": Chat, "agent": AgentSession}


class WakeupError(ValueError):
    """A wakeup request was refused; the message is safe to show the model."""


def _column(kind: WakeupKind) -> InstrumentedAttribute[int | None]:
    return {
        "topic": Wakeup.topic_id,
        "chat": Wakeup.chat_id,
        "agent": Wakeup.agent_session_id,
    }[kind]


def target_of(wakeup: Wakeup) -> tuple[WakeupKind, int]:
    if wakeup.topic_id is not None:
        return "topic", wakeup.topic_id
    if wakeup.chat_id is not None:
        return "chat", wakeup.chat_id
    assert wakeup.agent_session_id is not None
    return "agent", wakeup.agent_session_id


def _aware(value: datetime) -> datetime:
    """SQLite hands timestamps back naive; they are stored UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_at(value: str) -> datetime:
    """ISO 8601, a trailing ``Z`` allowed; a naive time is taken as UTC."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WakeupError(f"`at` is not an ISO 8601 date-time: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def resolve_wake_at(
    *, delay_seconds: float | None, at: str | None, now: datetime | None = None
) -> datetime:
    """When to wake up, from exactly one of a delay or an absolute time."""
    now = now or utcnow()
    if (delay_seconds is None) == (at is None or not at.strip()):
        raise WakeupError("Give exactly one of `delay_seconds` or `at`.")
    wake_at = now + timedelta(seconds=delay_seconds) if delay_seconds is not None else None
    wake_at = wake_at or _parse_at(at or "")
    if wake_at - now < MIN_DELAY:
        raise WakeupError(f"A wakeup must be at least {int(MIN_DELAY.total_seconds())}s away.")
    if wake_at - now > MAX_DELAY:
        raise WakeupError(f"A wakeup can be at most {MAX_DELAY.days} days away.")
    return wake_at


async def get_wakeup(session: AsyncSession, kind: WakeupKind, target_id: int) -> Wakeup | None:
    result = await session.execute(select(Wakeup).where(_column(kind) == target_id))
    return result.scalar_one_or_none()


async def schedule(
    session: AsyncSession,
    kind: WakeupKind,
    target_id: int,
    *,
    wake_at: datetime,
    prompt: str,
    reason: str | None = None,
) -> Wakeup:
    """Pause a conversation until ``wake_at``, replacing any wakeup it had."""
    prompt = prompt.strip()
    if not prompt:
        raise WakeupError("`prompt` must not be empty: it is what you will be woken up with.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise WakeupError(f"`prompt` is limited to {MAX_PROMPT_CHARS} characters.")
    if await session.get(_MODELS[kind], target_id) is None:
        raise WakeupError(f"No {kind} with id {target_id}.")
    wakeup = await get_wakeup(session, kind, target_id)
    if wakeup is None:
        wakeup = Wakeup(**{_column(kind).key: target_id})
        session.add(wakeup)
    wakeup.wake_at = wake_at
    wakeup.prompt = prompt
    wakeup.reason = (reason or "").strip() or None
    await session.commit()
    await session.refresh(wakeup)
    await publish_wakeup_changed()
    return wakeup


async def cancel(session: AsyncSession, kind: WakeupKind, target_id: int) -> bool:
    """Drop a conversation's wakeup; True when it had one."""
    result = await session.execute(delete(Wakeup).where(_column(kind) == target_id))
    await session.commit()
    if not result.rowcount:  # type: ignore[attr-defined]
        return False
    await publish_wakeup_changed()
    return True


async def wake_now(session: AsyncSession, wakeup: Wakeup) -> None:
    """Make a wakeup due, so the next tick wakes the conversation up."""
    wakeup.wake_at = utcnow()
    await session.commit()
    await publish_wakeup_changed()


async def due(session: AsyncSession) -> list[Wakeup]:
    result = await session.execute(
        select(Wakeup).where(Wakeup.wake_at <= utcnow()).order_by(Wakeup.wake_at)
    )
    return list(result.scalars().all())


async def list_pending(session: AsyncSession) -> list[WakeupRead]:
    """Every paused conversation, soonest wakeup first, with its title."""
    rows = (await session.execute(select(Wakeup).order_by(Wakeup.wake_at))).scalars().all()
    items: list[WakeupRead] = []
    for wakeup in rows:
        kind, target_id = target_of(wakeup)
        target = await session.get(_MODELS[kind], target_id)
        items.append(
            WakeupRead(
                id=wakeup.id,
                kind=kind,
                target_id=target_id,
                title=getattr(target, "title", None) or "",
                wake_at=wakeup.wake_at,
                prompt=wakeup.prompt,
                reason=wakeup.reason,
                created_at=wakeup.created_at,
            )
        )
    return items


async def resolve_public_id(session: AsyncSession, public_id: str) -> tuple[WakeupKind, int] | None:
    """The conversation (topic, chat or agent) a public id names."""
    kind: WakeupKind
    for kind in ("agent", "topic", "chat"):
        model = _MODELS[kind]
        row = await session.execute(select(model.id).where(model.public_id == public_id.strip()))
        found = row.scalar_one_or_none()
        if found is not None:
            return kind, found
    return None


def wake_message(wakeup: Wakeup) -> str:
    """The user turn a wakeup sends: marked, so nobody mistakes it for the user."""
    paused = _aware(wakeup.updated_at).strftime("%Y-%m-%d %H:%M UTC")
    return f"⏰ Self wake-up (scheduled at {paused}): {wakeup.prompt}"


def as_dict(wakeup: Wakeup) -> dict[str, Any]:
    """What a tool returns about a scheduled wakeup."""
    kind, target_id = target_of(wakeup)
    return {
        "scheduled": True,
        "kind": kind,
        "target_id": target_id,
        "wake_at": _aware(wakeup.wake_at).isoformat(),
        "now": utcnow().isoformat(),
        "prompt": wakeup.prompt,
        "reason": wakeup.reason,
        "note": "The conversation is paused. End your turn now; you will be woken up "
        "with the prompt above.",
    }
