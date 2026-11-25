"""Wakeup schemas: conversations a model paused until a later time."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from precursor.backend.schemas.schedule import UtcDateTime

WakeupKind = Literal["topic", "chat", "agent"]


class WakeupRead(BaseModel):
    """A paused conversation and when (and with what prompt) it wakes up."""

    id: int
    kind: WakeupKind
    # The topic, chat or agent session id, per ``kind``.
    target_id: int
    title: str
    wake_at: UtcDateTime
    prompt: str
    reason: str | None = None
    created_at: UtcDateTime
