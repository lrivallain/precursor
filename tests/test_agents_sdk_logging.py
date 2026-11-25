"""The SDK's expected shutdown noise stays out of the logs; real errors don't."""

from __future__ import annotations

import logging
import sys

import pytest

from precursor.backend.services.agents.sdk_logging import _TeardownPipeNoiseFilter


class ProcessExitedError(Exception):
    """Same name as the SDK 2.0 error the filter matches."""


def _record(exc: BaseException) -> logging.LogRecord:
    try:
        raise exc
    except BaseException:
        info = sys.exc_info()
    return logging.LogRecord("copilot._jsonrpc", logging.WARNING, __file__, 1, "x", None, info)


@pytest.mark.parametrize(
    "exc",
    [BrokenPipeError(), ProcessExitedError("CLI process exited with code 0")],
)
def test_teardown_errors_are_dropped(exc: BaseException) -> None:
    assert _TeardownPipeNoiseFilter().filter(_record(exc)) is False


def test_a_chained_teardown_error_is_dropped() -> None:
    try:
        try:
            raise ProcessExitedError("exited")
        except ProcessExitedError as inner:
            raise RuntimeError("request failed") from inner
    except RuntimeError as outer:
        record = _record(outer)
    assert _TeardownPipeNoiseFilter().filter(record) is False


def test_other_errors_are_kept() -> None:
    assert _TeardownPipeNoiseFilter().filter(_record(ValueError("real problem"))) is True
