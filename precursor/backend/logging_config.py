"""Central logging configuration.

A single ``logging.config.dictConfig`` applied at process startup (and handed to
uvicorn as its ``log_config``) so that every record — application, uvicorn, and
third-party (httpx, mcp, watchfiles) — shares one human-readable line:

    2026-06-16T12:09:46Z INFO     precursor.backend.services.scheduler Scheduler started

This replaces the previous mix of ad-hoc formats with missing timestamps and
levels. Modules acquire loggers with ``logging.getLogger(__name__)`` so the
component is always derivable from the logger name and propagation funnels every
record through the single root handler.
"""

from __future__ import annotations

import logging
import logging.config
import time
from typing import Any

# Third-party loggers that are chatty at INFO (request lines, file-watch
# notices). Pinned to WARNING so the terminal stays scannable; they still flow
# through the shared formatter when they do emit.
_NOISY_LOGGERS = ("httpx", "httpcore", "watchfiles", "watchfiles.main")

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


class UTCFormatter(logging.Formatter):
    """ISO-8601 timestamps in UTC with a trailing ``Z`` (e.g. ``2026-06-16T12:09:46Z``)."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z"


def build_log_config(log_level: str) -> dict[str, Any]:
    """Return a ``dictConfig`` mapping that unifies app, uvicorn, and library logs.

    uvicorn / uvicorn.access / uvicorn.error and the noted third-party loggers
    are given no handlers and ``propagate=True`` so the single root handler
    formats them — this is what collapses the previously divergent formats (and
    the duplicate rich-formatted MCP line) into one.
    """
    level = log_level.upper()
    return {
        "version": 1,
        # Our module-level loggers are created at import time, before this runs;
        # keep them alive so they propagate to the root handler.
        "disable_existing_loggers": False,
        "formatters": {
            "precursor": {
                "()": "precursor.backend.logging_config.UTCFormatter",
                "format": _FORMAT,
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "precursor",
                "stream": "ext://sys.stderr",
            },
        },
        "root": {"handlers": ["default"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "level": level, "propagate": True},
            "uvicorn.error": {"handlers": [], "level": level, "propagate": True},
            "uvicorn.access": {"handlers": [], "level": level, "propagate": True},
            # Clearing mcp's handlers here drops the duplicate rich-formatted
            # line; it then logs once through the root formatter.
            "mcp": {"handlers": [], "level": "INFO", "propagate": True},
            **{
                name: {"handlers": [], "level": "WARNING", "propagate": True}
                for name in _NOISY_LOGGERS
            },
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
