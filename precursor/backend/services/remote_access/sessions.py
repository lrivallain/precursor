"""Remote access state: the on/off switch and the paired devices' sessions.

Stored as one JSON ``AppSetting`` row. Only a SHA-256 of each session token is
kept, so a copy of the database doesn't let anyone in.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import weakref
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.clock import utcnow
from precursor.backend.models import AppSetting
from precursor.backend.services.setting_rows import store_setting

SETTING_KEY = "remote_access"
SESSION_TTL = timedelta(days=30)
# Sliding expiry: `last_used` is written back at most this often, so a phone
# streaming events doesn't turn every request into a database write.
TOUCH_EVERY = timedelta(hours=1)
MAX_SESSIONS = 10
_LABEL_MAX = 120


@dataclass
class RemoteSession:
    id: str
    token_hash: str
    label: str
    created_at: str
    last_used_at: str

    def expired(self, now: datetime) -> bool:
        return now - datetime.fromisoformat(self.last_used_at) > SESSION_TTL


@dataclass
class RemoteAccessState:
    enabled: bool = False
    sessions: list[RemoteSession] = field(default_factory=list)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _parse(raw: object) -> RemoteAccessState:
    if not isinstance(raw, dict):
        return RemoteAccessState()
    sessions = []
    for item in raw.get("sessions") or []:
        try:
            sessions.append(RemoteSession(**item))
        except TypeError:
            continue  # a malformed entry is dropped, not fatal
    return RemoteAccessState(enabled=raw.get("enabled") is True, sessions=sessions)


async def load(session: AsyncSession) -> RemoteAccessState:
    row = await session.get(AppSetting, SETTING_KEY)
    if row is None:
        return RemoteAccessState()
    try:
        return _parse(json.loads(row.value))
    except json.JSONDecodeError:
        return RemoteAccessState()


async def _save(session: AsyncSession, state: RemoteAccessState) -> None:
    """Persist ``state``, dropping expired sessions; the caller commits."""
    now = utcnow()
    state.sessions = [s for s in state.sessions if not s.expired(now)]
    payload = {"enabled": state.enabled, "sessions": [asdict(s) for s in state.sessions]}
    await store_setting(session, SETTING_KEY, payload)


def issue(state: RemoteAccessState, label: str) -> str:
    """Add a session to ``state`` and return its token (shown once, never stored)."""
    now = utcnow().isoformat()
    token = secrets.token_urlsafe(32)
    cleaned = "".join(ch for ch in label if ch.isprintable()).strip()[:_LABEL_MAX]
    state.sessions.append(
        RemoteSession(
            id=secrets.token_hex(8),
            token_hash=hash_token(token),
            label=cleaned or "Unknown device",
            created_at=now,
            last_used_at=now,
        )
    )
    # Oldest first: past the cap, the least recently created device goes.
    state.sessions = state.sessions[-MAX_SESSIONS:]
    return token


def find(state: RemoteAccessState, token: str | None) -> RemoteSession | None:
    """The live session ``token`` belongs to, if any."""
    if not token:
        return None
    wanted = hash_token(token)
    now = utcnow()
    for entry in state.sessions:
        if secrets.compare_digest(entry.token_hash, wanted) and not entry.expired(now):
            return entry
    return None


def needs_touch(entry: RemoteSession) -> bool:
    return utcnow() - datetime.fromisoformat(entry.last_used_at) > TOUCH_EVERY


def revoke(state: RemoteAccessState, session_id: str | None = None) -> bool:
    """Drop one session (or all of them with ``None``); whether anything went."""
    before = len(state.sessions)
    state.sessions = [] if session_id is None else [s for s in state.sessions if s.id != session_id]
    return len(state.sessions) != before


# One writer at a time: every change is a read-modify-write of the whole row,
# so two interleaved writers would undo each other (a revoked device coming
# back, a new pairing lost). One lock per event loop: tests run several.
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


async def update[T](mutate: Callable[[RemoteAccessState], T]) -> tuple[RemoteAccessState, T]:
    """Load, change and commit the state as one serialized step.

    ``mutate`` changes the state it is given in place and returns a result.
    Uses its own database session so the commit can't be mixed with a request's.
    """
    from precursor.backend.db import SessionLocal

    async with _lock(), SessionLocal() as db:
        state = await load(db)
        result = mutate(state)
        await _save(db, state)
        await db.commit()
        return state, result


def touch(state: RemoteAccessState, token: str | None) -> RemoteSession | None:
    """Mark the session ``token`` as used now; ``None`` if it is gone."""
    entry = find(state, token)
    if entry is not None:
        entry.last_used_at = utcnow().isoformat()
    return entry
