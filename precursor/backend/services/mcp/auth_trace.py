"""One channel for everything that happens to a WorkIQ OAuth credential.

The WorkIQ family sign-in is spread across five modules — the keep-alive ticker
decides a token is stale, ``workiq_preview`` drives the grant, the MCP SDK's
``OAuthClientProvider`` performs the actual refresh, the client manager records a
``needs_auth`` verdict, and the SPA re-triggers a hands-free pass. Each of them
logged (or didn't) in its own idiom, on its own logger, with no shared identity,
so reconstructing *why* a user was asked to sign in again meant correlating
half-sentences across a whole terminal scrollback. The one fact that actually
explains a lapse — Entra's ``AADSTS…`` code on the refused refresh — was never
recorded at all: the SDK logs only the HTTP status.

This module is the single place all of that reports to:

* **A dedicated logger**, ``precursor.mcp.auth``, whose level is set by
  :attr:`Settings.workiq_auth_log_level` independently of the app-wide
  ``log_level``. Auth tracing can therefore run at DEBUG on an app running at
  INFO — a lapse is rare and only reproducible in the wild, so the trace has to
  already be on when it happens. Every line is prefixed ``[workiq-auth]`` so a
  whole episode extracts with one ``grep``.
* **A bounded in-memory ring buffer** of the same records, structured, which
  :func:`snapshot` hands to ``GET /api/mcp/auth/diagnostics``. That's the
  copy-pasteable artefact: the terminal may have scrolled, been redirected, or
  belong to a packaged app the user never sees. Episode records and ambient
  background reporting are buffered *separately*, so the keep-alive's endless
  once-a-minute heartbeat can't evict the lines that explain a prompt.
* **Episodes.** Every record carries the id of the auth *episode* it belongs to
  (``wq-1a2b3c``), started when a credential first needs attention and closed
  when it is renewed or given up on, plus the elapsed milliseconds since that
  start. Episodes key on the *credential*, not the server, because the Agent 365
  pair shares one token and therefore one episode.

Records are keyed by credential and deliberately carry **no secrets**: never a
token, an authorization code, a ``state``/``nonce``, or the user's account name —
:func:`redact` reduces those to presence, length and shape. The buffer is meant
to be pasted into a bug report as-is.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

logger = logging.getLogger("precursor.mcp.auth")

# How many records to keep, per buffer. An auth episode is a few dozen lines, so
# the episode ring holds many consecutive failures; the ambient ring only has to
# cover enough recent background reporting to give an episode context.
_EPISODE_TRACE_LIMIT: Final = 500
_AMBIENT_TRACE_LIMIT: Final = 200

# Values long enough to be a credential are never logged verbatim. Entra
# ``error_description`` strings, on the other hand, are the whole point — they
# carry the AADSTS code — so they get a generous cap rather than redaction.
_DESCRIPTION_LIMIT: Final = 400


@dataclass(frozen=True, slots=True)
class AuthTraceRecord:
    """One observation about a credential, as stored in the ring buffer."""

    seq: int
    at: str
    credential: str
    server: str
    phase: str
    episode: str | None
    elapsed_ms: int | None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "credential": self.credential,
            "server": self.server,
            "phase": self.phase,
            "episode": self.episode,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
        }


# Two buffers, not one. Records made *during* an episode are the artefact this
# module exists to produce; everything else is ambient background reporting (a
# keep-alive tick that decided to do nothing, a credential flagged and
# unflagged). Sharing a single ring would let the ambient stream — which runs
# forever, on a timer — evict the handful of lines that explain a prompt long
# before anyone got round to reading them. Splitting them means an episode
# survives however long the app then sits idle.
_episode_records: deque[AuthTraceRecord] = deque(maxlen=_EPISODE_TRACE_LIMIT)
_ambient_records: deque[AuthTraceRecord] = deque(maxlen=_AMBIENT_TRACE_LIMIT)

# Monotonic sequence so the two buffers can be merged back into one timeline.
# Wall-clock timestamps are too coarse: several records can share a millisecond.
_seq: int = 0

# Open episodes, keyed by credential: ``(episode_id, started_monotonic)``.
_episodes: dict[str, tuple[str, float]] = {}


def _credential_of(server: str) -> str:
    # Imported here rather than at module scope: ``oauth_registry`` imports the
    # profile modules, which import this one for their own tracing.
    from precursor.backend.services.mcp.oauth_registry import credential_key

    return credential_key(server)


def begin_episode(server: str, reason: str, **detail: Any) -> str:
    """Open (or adopt) the auth episode for ``server``'s credential.

    An episode spans everything from "this credential needs attention" to "it is
    renewed, or we gave up": a keep-alive refresh, the hands-free silent pass,
    the self-opened browser prompt and the manual banner click are all legs of
    one story. Re-entering an open episode keeps the original id and clock so
    those legs stay stitched together rather than each looking like a fresh
    outage.
    """
    credential = _credential_of(server)
    existing = _episodes.get(credential)
    if existing is None:
        episode = f"wq-{uuid.uuid4().hex[:6]}"
        _episodes[credential] = (episode, time.monotonic())
        record(server, "episode opened", reason=reason, **detail)
    else:
        episode = existing[0]
        record(server, "episode continues", reason=reason, **detail)
    return episode


def end_episode(server: str, outcome: str, **detail: Any) -> None:
    """Close ``server``'s credential episode so the next one starts at zero."""
    credential = _credential_of(server)
    if credential not in _episodes:
        return
    record(server, "episode closed", outcome=outcome, **detail)
    _episodes.pop(credential, None)


def current_episode(server: str) -> str | None:
    """The open episode id for ``server``'s credential, if any."""
    existing = _episodes.get(_credential_of(server))
    return existing[0] if existing else None


def record(server: str, phase: str, *, level: int = logging.INFO, **detail: Any) -> None:
    """Log and buffer one step of a credential's story.

    ``level`` drops to ``DEBUG`` for per-tick chatter (a keep-alive that decided
    to do nothing) so the INFO stream stays a readable narrative of the
    transitions that matter, while the ring buffers keep everything either way —
    the diagnostics endpoint should not lose detail to a log level.
    """
    global _seq
    credential = _credential_of(server)
    episode_state = _episodes.get(credential)
    episode = episode_state[0] if episode_state else None
    elapsed_ms = (
        None if episode_state is None else round((time.monotonic() - episode_state[1]) * 1000)
    )
    clean = {key: redact(key, value) for key, value in detail.items() if value is not None}

    _seq += 1
    entry = AuthTraceRecord(
        seq=_seq,
        at=datetime.now(UTC).isoformat(),
        credential=credential,
        server=server,
        phase=phase,
        episode=episode,
        elapsed_ms=elapsed_ms,
        detail=clean,
    )
    (_episode_records if episode else _ambient_records).append(entry)

    stamp = "" if elapsed_ms is None else f" +{elapsed_ms}ms"
    tag = f"[{episode}] " if episode else ""
    suffix = " " + " ".join(f"{k}={v!r}" for k, v in clean.items()) if clean else ""
    logger.log(level, "[workiq-auth] %s%s%s — %s%s", tag, server, stamp, phase, suffix)


# Keys whose values are credentials or personally identifying, whatever they
# look like. Reduced to a shape rather than dropped, because "the refresh token
# was present but 8 chars long" is a diagnosis and "absent" is a different one.
_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "code",
        "authorization_code",
        "state",
        "nonce",
        "code_verifier",
        "login_hint",
        "upn",
        "email",
        "account",
    }
)


def redact(key: str, value: Any) -> Any:
    """Reduce a value that must not be logged verbatim to a safe shape.

    Secrets and account names become ``"<present:N chars>"``; Entra's
    ``error_description`` is truncated rather than hidden (it carries the AADSTS
    code the whole trace exists to capture).
    """
    if key in _SECRET_KEYS:
        if not value:
            return "<absent>"
        return f"<present:{len(str(value))} chars>"
    if key.endswith("description") and isinstance(value, str):
        return value[:_DESCRIPTION_LIMIT]
    return value


def snapshot(limit: int | None = None) -> list[dict[str, Any]]:
    """The buffered records as one timeline, oldest-first.

    ``limit`` is applied to *each* buffer rather than to the merged result, so a
    long quiet stretch of background reporting can never push an episode out of
    the answer — the whole point of keeping them apart.
    """

    def _tail(records: deque[AuthTraceRecord]) -> list[AuthTraceRecord]:
        items = list(records)
        return items[-limit:] if limit is not None and limit > 0 else items

    merged = sorted([*_tail(_episode_records), *_tail(_ambient_records)], key=lambda r: r.seq)
    return [item.as_dict() for item in merged]


def reset() -> None:
    """Drop every buffered record and open episode (tests)."""
    _episode_records.clear()
    _ambient_records.clear()
    _episodes.clear()
