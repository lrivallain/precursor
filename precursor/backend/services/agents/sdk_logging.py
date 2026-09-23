"""Keep the Copilot SDK's expected shutdown noise out of the logs.

A leaf module: it must never import ``services.agents.manager``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator

# Broken-pipe family raised when a JSON-RPC write races the CLI child's stdin
# closing during shutdown. The SDK spawns the Copilot CLI in *our* process group
# (no ``start_new_session``), so on Ctrl+C the child takes the same terminal
# SIGINT and can exit — closing its stdin — before our graceful ``client.stop()``
# finishes its ``runtime.shutdown`` request. The SDK already swallows the write
# error (into a ``StopError`` we suppress), but logs it at WARNING with a full
# traceback first: pure noise on an otherwise-clean shutdown.
_TEARDOWN_PIPE_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    EOFError,
)

# copilot SDK loggers that emit those teardown write failures.
_SDK_PIPE_LOGGERS = ("copilot._jsonrpc", "copilot.client")


class _TeardownPipeNoiseFilter(logging.Filter):
    """Drop copilot SDK records whose exception is an expected teardown pipe error."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc: BaseException | None = record.exc_info[1] if record.exc_info else None
        while exc is not None:
            if isinstance(exc, _TEARDOWN_PIPE_ERRORS):
                return False
            # Walk the chain — the SDK may wrap/chain the underlying pipe error.
            exc = exc.__cause__ or exc.__context__
        return True


@contextlib.contextmanager
def _quiet_sdk_teardown_pipe_noise() -> Iterator[None]:
    """Silence the expected broken-pipe tracebacks the SDK logs while stopping."""
    noise_filter = _TeardownPipeNoiseFilter()
    sdk_loggers = [logging.getLogger(name) for name in _SDK_PIPE_LOGGERS]
    for sdk_logger in sdk_loggers:
        sdk_logger.addFilter(noise_filter)
    try:
        yield
    finally:
        for sdk_logger in sdk_loggers:
            sdk_logger.removeFilter(noise_filter)
