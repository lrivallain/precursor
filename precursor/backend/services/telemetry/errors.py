"""Error reports: which exception, raised where in Precursor's code.

A logging handler on the root logger sees every record logged with an
exception (``logger.exception``, uvicorn's unhandled-request trace). It keeps
the exception's type and the frames that belong to the ``precursor`` package,
as a path relative to it, a function name and a line number. It never keeps
the exception's message or the log line: both routinely carry prompts, file
paths, URLs and tokens.

Reports wait in memory until the ticker sends them. Each distinct error is sent
once a day at most, and only so many a day, so a crash loop costs one row.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

import precursor
from precursor.backend.services.telemetry.appinsights import Target, envelope

_PACKAGE_ROOT = Path(precursor.__file__).resolve().parent
_MAX_FRAMES = 30
MAX_REPORTS_PER_DAY = 20
_LOGGER_NAME = re.compile(r"^[A-Za-z_][\w.]{0,99}$")


@dataclass(frozen=True)
class Frame:
    file: str
    function: str
    line: int


@dataclass(frozen=True)
class ErrorReport:
    fingerprint: str
    type_name: str
    logger: str
    frames: tuple[Frame, ...]


def _own_frames(tb: TracebackType | None) -> tuple[Frame, ...]:
    frames: list[Frame] = []
    for summary in traceback.extract_tb(tb):
        try:
            relative = Path(summary.filename).resolve().relative_to(_PACKAGE_ROOT)
        except ValueError:
            continue
        frames.append(Frame(relative.as_posix(), summary.name, summary.lineno or 0))
    # The innermost frames say where it broke; keep those when trimming.
    return tuple(frames[-_MAX_FRAMES:])


def _type_name(exc_type: type[BaseException]) -> str:
    module = exc_type.__module__
    return exc_type.__qualname__ if module == "builtins" else f"{module}.{exc_type.__qualname__}"


def report_from(record: logging.LogRecord) -> ErrorReport | None:
    """A sanitised report of the exception on ``record``, if it has one."""
    if not record.exc_info or record.exc_info[0] is None:
        return None
    exc_type, _exc, tb = record.exc_info
    frames = _own_frames(tb)
    type_name = _type_name(exc_type)
    digest = hashlib.sha256(
        "|".join([type_name, *(f"{f.file}:{f.function}:{f.line}" for f in frames)]).encode()
    ).hexdigest()[:16]
    logger_name = record.name if _LOGGER_NAME.match(record.name) else "other"
    return ErrorReport(digest, type_name, logger_name, frames)


class _Collector:
    """Reports waiting to be sent, deduplicated per UTC day."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, ErrorReport] = {}
        self._seen: set[str] = set()
        self._day = ""
        self.enabled = False

    def _roll(self) -> None:
        today = datetime.now(UTC).date().isoformat()
        if today != self._day:
            self._day = today
            self._seen.clear()

    def add(self, report: ErrorReport) -> None:
        with self._lock:
            self._roll()
            if report.fingerprint in self._seen or len(self._seen) >= MAX_REPORTS_PER_DAY:
                return
            self._seen.add(report.fingerprint)
            self._pending[report.fingerprint] = report

    def drain(self) -> list[ErrorReport]:
        with self._lock:
            reports = list(self._pending.values())
            self._pending.clear()
            return reports

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.enabled = enabled
            if not enabled:
                self._pending.clear()


collector = _Collector()


class TelemetryErrorHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        if not collector.enabled or record.name.startswith(__package__ or "telemetry"):
            return
        try:
            report = report_from(record)
        except Exception:  # pragma: no cover - a report must never break logging
            return
        if report is not None:
            collector.add(report)


_handler: TelemetryErrorHandler | None = None


def install_handler() -> None:
    """Attach the handler to the root logger, once per process."""
    global _handler
    if _handler is None:
        _handler = TelemetryErrorHandler()
        logging.getLogger().addHandler(_handler)


def uninstall_handler() -> None:
    global _handler
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler = None


def build(target: Target, report: ErrorReport, tags: dict[str, str]) -> dict[str, Any]:
    stack = [
        {"level": level, "method": f.function, "fileName": f.file, "line": f.line}
        # Application Insights lists the innermost frame first.
        for level, f in enumerate(reversed(report.frames))
    ]
    return envelope(
        target,
        kind="Exception",
        base_type="ExceptionData",
        base_data={
            "exceptions": [
                {
                    "id": 1,
                    "typeName": report.type_name,
                    # Required by the schema; the real message is never sent.
                    "message": report.type_name,
                    "hasFullStack": bool(stack),
                    "parsedStack": stack,
                }
            ],
            "severityLevel": 3,
            "problemId": report.fingerprint,
            "properties": {"logger": report.logger, "fingerprint": report.fingerprint},
        },
        tags=tags,
    )
