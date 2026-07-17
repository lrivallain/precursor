"""Cockpit model — a user-registered local webapp launched on demand.

A "cockpit" is a locally-run web application (someone's dashboard, tool, or
dev server) that the user registers with a **run command** and the **port** it
listens on. On start, Precursor spawns the command in its own process group,
polls the port until it accepts connections, and embeds the app in an iframe
via the backend reverse proxy (with an open-in-tab fallback).

Only the *definition* is persisted here. Runtime state (pid, status, logs) is
ephemeral and owned by the in-memory ``CockpitManager`` — a process can never
outlive the backend, so there is nothing durable to store about it.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class Cockpit(Base, TimestampMixin):
    __tablename__ = "cockpits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # URL/identifier-friendly handle, unique across cockpits.
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # Free-text note shown in the UI (how to use it, what it does).
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The shell command that starts the cockpit (run via the login shell).
    command: Mapped[str] = mapped_column(Text, nullable=False)
    # Working directory the command runs in; null = the backend's cwd.
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The loopback port the cockpit listens on once started (user-declared).
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    # Extra environment variables as a JSON object string ({"KEY": "value"}).
    # Merged over the backend's environment when spawning.
    env: Mapped[str | None] = mapped_column(Text, nullable=True)
