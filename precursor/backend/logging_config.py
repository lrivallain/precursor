"""Central logging configuration.

A single ``logging.config.dictConfig`` applied at process startup (and handed to
uvicorn as its ``log_config``) so that every record — application, uvicorn, and
third-party — shares one human-readable line:

    2026-06-16T12:09:46Z INFO     precursor.backend.services.scheduler Scheduler started

Two design points:

* **Debug stays app-only.** The root level follows the configured ``log_level``
  (so ``precursor.*`` loggers honour ``debug``), but noisy third-party loggers
  (aiosqlite, SQLAlchemy, sse-starlette, …) are pinned to fixed floors that
  *ignore* the app level — turning on app DEBUG never unleashes library DEBUG
  spam.
* **Colour when it helps.** When stderr is a TTY the level is ANSI-coloured and
  the timestamp/name are dimmed; when output is piped or redirected the colour
  is dropped so logs stay grep-clean. No third-party dependency is used.
"""

from __future__ import annotations

import logging
import logging.config
import sys
import time
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


def build_log_config(log_level: str, *, color: bool | None = None) -> dict[str, Any]:
    """Return a ``dictConfig`` mapping that unifies app, uvicorn, and library logs.

    ``color`` defaults to auto-detection (stderr is a TTY). uvicorn and the
    pinned third-party loggers are given no handlers and ``propagate=True`` so
    the single root handler formats them uniformly; their *levels* come from
    ``_THIRD_PARTY_LEVELS`` so they ignore the app ``log_level``.
    """
    level = log_level.upper()
    use_color = sys.stderr.isatty() if color is None else color
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
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "precursor",
                "stream": "ext://sys.stderr",
            },
        },
        # Root level drives the app's own precursor.* loggers (honours `debug`).
        "root": {"handlers": ["default"], "level": level},
        "loggers": {
            name: {"handlers": [], "level": lvl, "propagate": True}
            for name, lvl in _THIRD_PARTY_LEVELS.items()
        },
    }


def configure_logging(log_level: str) -> dict[str, Any]:
    """Apply the shared config now and return it for uvicorn's ``log_config``.

    Applying it immediately means early startup logs (before uvicorn boots) are
    already formatted; passing the same dict to ``uvicorn.run`` keeps the format
    in the reload subprocess too.
    """
    cfg = build_log_config(log_level)
    logging.config.dictConfig(cfg)
    return cfg
