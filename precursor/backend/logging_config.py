"""Central logging configuration.

A single ``logging.config.dictConfig`` applied at process startup (and handed to
uvicorn as its ``log_config``) so that every record — application, uvicorn, and
third-party — shares one human-readable line:

    2026-06-16T12:09:46Z INFO     precursor.backend.services.scheduler Scheduler started

Three design points:

* **Debug stays app-only.** The root level follows the configured ``log_level``
  (so ``precursor.*`` loggers honour ``debug``), but noisy third-party loggers
  (aiosqlite, SQLAlchemy, sse-starlette, …) are pinned to fixed floors that
  *ignore* the app level — turning on app DEBUG never unleashes library DEBUG
  spam. One logger is pinned the other way: the WorkIQ auth trace
  (``precursor.mcp.auth``) follows ``workiq_auth_log_level`` so it can stay
  verbose on an app running at INFO.
* **Colour when it helps.** When stderr is a TTY the level is ANSI-coloured and
  the timestamp/name are dimmed; when output is piped or redirected the colour
  is dropped so logs stay grep-clean. No third-party dependency is used.
* **The process writes its own log file.** Passing ``log_file`` adds a rotating
  file handler, so ``precursor.log`` is written by the app itself rather than by
  whoever happens to own its stdio. That is what makes the file the *same* file
  in every mode — foreground, a supervisor-spawned child, a launchd agent, a
  systemd user unit — instead of only in the one where Precursor spawned the
  process and redirected the pipe. See :func:`build_log_config`.
"""

from __future__ import annotations

import logging
import logging.config
import sys
import time
from pathlib import Path
from typing import Any

# Third-party loggers pinned to a fixed level regardless of the app log_level,
# so running the app at DEBUG doesn't drown the terminal in library internals.
# (aiosqlite/SQLAlchemy emit per-statement DEBUG; sse-starlette logs every ping
# and chunk at DEBUG; uvicorn.access at INFO is the useful request line.)
_THIRD_PARTY_LEVELS: dict[str, str] = {
    "uvicorn": "INFO",
    "uvicorn.error": "INFO",
    "uvicorn.access": "INFO",
    "mcp": "INFO",
    # The streamable-http / stdio client transports log session IDs, protocol
    # negotiation and reconnect attempts at INFO on every connection — routine
    # chatter, so quiet it to WARNING (real failures still surface).
    "mcp.client": "WARNING",
    "httpx": "WARNING",
    "httpcore": "WARNING",
    "watchfiles": "WARNING",
    "watchfiles.main": "WARNING",
    "aiosqlite": "WARNING",
    "sqlalchemy": "WARNING",
    "sqlalchemy.engine": "WARNING",
    "sse_starlette": "INFO",
    "sse_starlette.sse": "INFO",
    "openai": "WARNING",
    "openai._base_client": "WARNING",
    "asyncio": "WARNING",
}

# The WorkIQ auth trace (services/mcp/auth_trace.py). Pinned like the loggers
# above, but to stay *louder* than the app level rather than quieter — see
# ``build_log_config``.
_AUTH_LOGGER = "precursor.mcp.auth"

# Rotation defaults for the file handler, overridable via Settings. Five
# megabytes over three generations keeps a couple of weeks of an ordinary
# instance without the unbounded growth an unrotated service-manager capture
# gets (a real one reached 15 MB in a week).
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 3

# The canonical log file, relative to ``Settings.logs_dir``. The tray,
# ``precursor service logs`` and the runtime state file all name this one file,
# so it lives here rather than being spelled out at each call site.
LOG_FILENAME = "precursor.log"
TRAY_LOG_FILENAME = "tray.log"

# ANSI styling (only emitted when stderr is a TTY).
_RESET = "\033[0m"
_DIM = "\033[2m"
_LEVEL_COLORS = {
    "DEBUG": "\033[36m",  # cyan
    "INFO": "\033[32m",  # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[1;31m",  # bold red
}


class UTCFormatter(logging.Formatter):
    """One-line formatter: ISO-8601 UTC timestamp (``Z``), level, name, message.

    With ``color=True`` the level is coloured by severity and the timestamp and
    logger name are dimmed; otherwise the output is plain text.
    """

    def __init__(self, *, color: bool = False) -> None:
        super().__init__()
        self.color = color

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z"

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record)
        level = f"{record.levelname:<8}"
        name = record.name
        message = record.getMessage()
        if self.color:
            ts = f"{_DIM}{ts}{_RESET}"
            level = f"{_LEVEL_COLORS.get(record.levelname, '')}{level}{_RESET}"
            name = f"{_DIM}{name}{_RESET}"
        line = f"{ts} {level} {name} {message}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line


def build_log_config(
    log_level: str,
    *,
    color: bool | None = None,
    auth_log_level: str | None = None,
    log_file: str | Path | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
    console: bool | None = None,
) -> dict[str, Any]:
    """Return a ``dictConfig`` mapping that unifies app, uvicorn, and library logs.

    ``color`` defaults to auto-detection (stderr is a TTY). uvicorn and the
    pinned third-party loggers are given no handlers and ``propagate=True`` so
    the single root handler formats them uniformly; their *levels* come from
    ``_THIRD_PARTY_LEVELS`` so they ignore the app ``log_level``.

    ``auth_log_level`` pins :data:`_AUTH_LOGGER` the same way but in the opposite
    direction: the WorkIQ auth trace stays verbose (DEBUG by default) on an app
    running at INFO, because a sign-in lapse is rare and can't be reproduced on
    demand — the trace has to already be on when it happens.

    ``log_file`` adds a size-rotating file handler writing plain (never
    ANSI-coloured) lines. ``console`` then defaults to *whether stderr is a
    TTY*, which is the point of the pairing: in a terminal you want both, but
    under launchd or systemd stderr is already being captured to a file of the
    service manager's choosing, and keeping it would write a second, unrotated
    copy of every line — which is how ``launchd.app.err.log`` grows to tens of
    megabytes. With no ``log_file`` the console handler is always kept, because
    dropping it would leave the process with nowhere to log at all.
    """
    level = log_level.upper()
    use_color = sys.stderr.isatty() if color is None else color
    if console is None:
        console = sys.stderr.isatty() if log_file is not None else True
    # Never leave a process mute: a file we could not open is worse than colour
    # codes in a redirect.
    console = console or log_file is None
    pinned = dict(_THIRD_PARTY_LEVELS)
    if auth_log_level:
        pinned[_AUTH_LOGGER] = auth_log_level.upper()
    handlers: dict[str, dict[str, Any]] = {}
    if console:
        handlers["default"] = {
            "class": "logging.StreamHandler",
            "formatter": "precursor",
            "stream": "ext://sys.stderr",
        }
    if log_file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "precursor_plain",
            "filename": str(log_file),
            "maxBytes": max_bytes,
            "backupCount": backup_count,
            "encoding": "utf-8",
            # The handler is opened by dictConfig, in a process that may sit
            # idle for days before its first record; deferring keeps a rotated
            # file from being held open for nothing.
            "delay": True,
        }
    return {
        "version": 1,
        # Our module-level loggers are created at import time, before this runs;
        # keep them alive so they propagate to the root handler.
        "disable_existing_loggers": False,
        "formatters": {
            "precursor": {
                "()": "precursor.backend.logging_config.UTCFormatter",
                "color": use_color,
            },
            "precursor_plain": {
                "()": "precursor.backend.logging_config.UTCFormatter",
                "color": False,
            },
        },
        "handlers": handlers,
        # Root level drives the app's own precursor.* loggers (honours `debug`).
        "root": {"handlers": list(handlers), "level": level},
        "loggers": {
            name: {"handlers": [], "level": lvl, "propagate": True} for name, lvl in pinned.items()
        },
    }


def configure_logging(
    log_level: str,
    *,
    auth_log_level: str | None = None,
    log_file: str | Path | None = None,
    console: bool | None = None,
) -> dict[str, Any]:
    """Apply the shared config now and return it for uvicorn's ``log_config``.

    Applying it immediately means early startup logs (before uvicorn boots) are
    already formatted; passing the same dict to ``uvicorn.run`` keeps the format
    in the reload subprocess too. ``auth_log_level`` defaults to
    :attr:`Settings.workiq_auth_log_level`.

    ``log_file`` is created (with its parent directory) before the config is
    applied, and dropped — with a warning on the console — if that fails. A log
    destination is a convenience; refusing to boot over one would turn a full
    disk or a read-only data directory into an outage.
    """
    global _configured
    max_bytes = _DEFAULT_MAX_BYTES
    backup_count = _DEFAULT_BACKUP_COUNT
    if auth_log_level is None or log_file is not None:
        # Lazy so this module stays import-cheap and free of a config dependency
        # at top level (and so a broken .env can't break log formatting: the
        # defaults above are already usable).
        try:
            from precursor.backend.config import get_settings

            settings = get_settings()
            if auth_log_level is None:
                auth_log_level = settings.workiq_auth_log_level
            max_bytes = settings.log_file_max_bytes
            backup_count = settings.log_file_backups
        except Exception:  # pragma: no cover - defensive
            pass
    problem: str | None = None
    if log_file is not None:
        # Opened here rather than left to dictConfig, which would raise: a full
        # disk or a read-only data directory must cost you the log, not the app.
        try:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        except OSError as exc:
            problem = f"Could not open the log file {log_file} ({exc}) — logging to stderr only."
            log_file = None
    cfg = build_log_config(
        log_level,
        auth_log_level=auth_log_level,
        log_file=log_file,
        max_bytes=max_bytes,
        backup_count=backup_count,
        console=console,
    )
    logging.config.dictConfig(cfg)
    _configured = True
    if problem:
        logging.getLogger(__name__).warning(problem)
    return cfg


# Whether this process's logging is owned by :func:`configure_logging`. Alembic's
# ``env.py`` consults it: run from the CLI it should apply its own ``alembic.ini``
# logging, but run *inside the app* (``init_db`` upgrades to head on every
# startup) that same call would tear down the config above — see there.
_configured = False


def log_path(filename: str = LOG_FILENAME) -> Path:
    """The canonical log file for this data directory.

    One accessor so the writer (:func:`configure_logging` via the run paths),
    the readers (``precursor service logs``, the tray's *Open log file*) and the
    supervisor's ``runtime.json`` can never drift onto different files — which
    is exactly what happened while the file was only ever written by whoever
    owned the process's stdio.
    """
    from precursor.backend.config import get_settings

    return Path(get_settings().logs_dir) / filename


def logging_is_configured() -> bool:
    """Whether :func:`configure_logging` has run in this process."""
    return _configured


def configure_subprocess_logging() -> None:
    """Apply the shared config in a stdio MCP subprocess.

    The in-tree MCP servers (fetch / workspace-fs / cmd-runner / precursor) run
    as ``python -m …`` subprocesses; importing FastMCP installs a plain root
    StreamHandler, so without this their ``mcp.server`` logs print in a
    different, timestamp-less format. Calling this in each ``main()`` replaces
    that handler with the unified formatter and honours ``PRECURSOR_LOG_LEVEL``
    (the parent forwards the env).
    """
    # Imported lazily so this module stays import-cheap and free of a config
    # dependency at top level.
    from precursor.backend.config import get_settings

    configure_logging(get_settings().log_level)
