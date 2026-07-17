"""Cockpit model — a user-registered dashboard launched on demand.

A "cockpit" is one of two things:

* a **command** cockpit — a locally-run web app registered with a run command
  and the port it listens on. On start, Precursor spawns the command, polls the
  port, and embeds the app via the reverse proxy (with start/stop lifecycle).
* a **url** cockpit — a fixed URL embedded directly in an iframe. There is no
  process and no lifecycle; it's just a saved, embedded destination.

Only the *definition* is persisted here. For command cockpits, runtime state
(pid, status, logs) is ephemeral and owned by the in-memory ``CockpitManager``.
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
    # "command" — spawns a local process and embeds it via the reverse proxy
    #             (has start/stop lifecycle). Uses ``command``/``port``.
    # "url"     — embeds a fixed URL directly in the iframe. No process, no
    #             lifecycle controls. Uses ``url``.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="command", server_default="command"
    )
    # The shell command that starts the cockpit (run via the login shell).
    # Empty for ``url`` cockpits (which have no process); the API presents it as
    # null for those.
    command: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Working directory the command runs in; null = the backend's cwd.
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The loopback port the cockpit listens on once started (user-declared).
    # 0 for ``url`` cockpits; the API presents it as null for those.
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Extra environment variables as a JSON object string ({"KEY": "value"}).
    # Merged over the backend's environment when spawning.
    env: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The URL embedded directly for ``url`` cockpits. Null for ``command`` ones.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
