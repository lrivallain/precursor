"""Launcher port-availability probes must agree with uvicorn's bind semantics.

uvicorn binds its listen socket with ``SO_REUSEADDR``, so a port left in
``TIME_WAIT`` by a just-closed connection (e.g. an SSE chat stream torn down on
Ctrl-C) is still bindable. The launcher's pre-flight checks must use the same
option, otherwise a freshly stopped server reports its port as "in use" and
refuses to restart under ``--strict-port``.
"""

from __future__ import annotations

import socket

import pytest

from precursor.backend.__main__ import _port_free, _resolve_port


def _port_in_time_wait() -> int:
    """Return a loopback port with a connection left in ``TIME_WAIT``.

    Establishes a real connection and active-closes it from the side whose
    local port is the one under test, so the OS keeps that port in
    ``TIME_WAIT`` — exactly the state a server leaves behind when it closes a
    client stream on shutdown.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.socket()
    client.connect(("127.0.0.1", port))
    server_side, _ = listener.accept()
    # Active close on the server side puts local port `port` into TIME_WAIT.
    server_side.close()
    client.close()
    listener.close()
    return port


def _plain_bind_ok(host: str, port: int) -> bool:
    sock = socket.socket()  # deliberately no SO_REUSEADDR (old probe behaviour)
    try:
        sock.bind((host, port))
        sock.close()
        return True
    except OSError:
        return False


def test_time_wait_port_reproduces_old_failure() -> None:
    port = _port_in_time_wait()
    # Sanity-check the reproduction: a plain bind (the old probe) rejects the
    # TIME_WAIT port. If the platform doesn't exhibit this, the fix is moot here.
    if _plain_bind_ok("127.0.0.1", port):
        pytest.skip("platform does not hold this port in TIME_WAIT for bind()")
    # The fixed probe matches uvicorn (SO_REUSEADDR) and sees the port as free.
    assert _port_free("127.0.0.1", port) is True


def test_resolve_port_strict_accepts_time_wait_port() -> None:
    port = _port_in_time_wait()
    if _plain_bind_ok("127.0.0.1", port):
        pytest.skip("platform does not hold this port in TIME_WAIT for bind()")
    # --strict-port must not treat a TIME_WAIT port as busy.
    assert _resolve_port("127.0.0.1", port, strict=True) == port


def test_port_free_reports_busy_for_active_listener() -> None:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        # A live listener genuinely holds the port; SO_REUSEADDR does not let a
        # second bind succeed here, so this must still read as busy.
        assert _port_free("127.0.0.1", port) is False
    finally:
        listener.close()
