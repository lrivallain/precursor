"""``precursor`` command-line launcher.

Two modes:

* ``precursor`` — a single uvicorn process serving the JSON API **and** the
  pre-built SPA on one port. No Node.js runtime required; this is what runs
  from an installed wheel (e.g. ``uvx precursor``).
* ``precursor --dev`` — development stack: uvicorn with ``--reload`` plus the
  Vite dev server (HMR) on the API port + 1, which proxies ``/api`` back to
  uvicorn. Both processes are managed together and shut down on Ctrl-C. Requires
  a source checkout (``uv run precursor --dev``).
"""

from __future__ import annotations

import argparse
import errno
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from precursor.backend.config import get_settings
from precursor.backend.logging_config import configure_logging

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _wait_for_port(host: str, port: int, *, stop: threading.Event, timeout: float = 30.0) -> bool:
    """Block until ``host:port`` accepts a TCP connection (or timeout/stop).

    Uses ``create_connection`` so it works whether the target bound IPv4 or IPv6
    (Vite binds ``localhost`` → ``::1`` on macOS, uvicorn binds ``127.0.0.1``).
    """
    deadline = time.monotonic() + timeout
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            pass
        stop.wait(0.2)
    return False


def _loopback(host: str) -> str:
    """A concrete loopback address to probe/advertise for a wildcard bind host."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _port_free(host: str, port: int) -> bool:
    """Best-effort check that nothing holds ``port`` on loopback (racy).

    Checks both IPv4 (127.0.0.1) and IPv6 (::1) loopback because uvicorn binds
    the former and Vite the latter — a port "free" on one family may still be
    taken by a sibling instance on the other. Only ``EADDRINUSE`` counts as
    busy; unsupported/unavailable families are ignored so this still works on
    hosts without IPv6.
    """
    probes = [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]
    if host not in ("", "0.0.0.0", "::", "127.0.0.1", "::1", "localhost"):
        probes.append((socket.AF_INET6 if ":" in host else socket.AF_INET, host))
    for family, addr in probes:
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue  # address family unsupported on this host
        with sock:
            try:
                sock.bind((addr, port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    return False
                # Address not available / family quirk — ignore this probe.
    return True


def _resolve_port(
    host: str, preferred: int, *, strict: bool, avoid: frozenset[int] = frozenset()
) -> int:
    """Pick a bindable port.

    ``preferred == 0`` asks the OS for an ephemeral one. Otherwise try
    ``preferred`` and, unless ``strict``, scan upward for the next free port
    (skipping ``avoid``) so multiple instances don't collide. In ``strict`` mode
    a busy ``preferred`` is fatal.
    """
    bind_host = _loopback(host)
    if preferred == 0:
        family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((bind_host, 0))
            return int(sock.getsockname()[1])
    candidate = preferred
    for _ in range(100):
        if 0 < candidate <= 65535 and candidate not in avoid and _port_free(bind_host, candidate):
            return candidate
        if strict:
            raise SystemExit(
                f"Port {preferred} is in use on {bind_host}. Free it, choose another "
                "--port, or drop --strict-port to auto-select a free one."
            )
        candidate += 1
    raise SystemExit(f"Could not find a free port near {preferred} on {bind_host}.")


def _print_banner(*, title: str, open_url: str, api_url: str | None) -> None:
    line = "\u2500" * 54
    print(line, file=sys.stderr)
    print(f"  {title}", file=sys.stderr)
    print(f"  \u25b6 Open   {open_url}", file=sys.stderr)
    if api_url and api_url != open_url:
        print(f"    API    {api_url}", file=sys.stderr)
    print(line, file=sys.stderr)
    sys.stderr.flush()


def _announce_when_ready(
    *,
    connect_host: str,
    ready_port: int,
    title: str,
    open_url: str,
    api_url: str | None,
    open_browser: bool,
    stop: threading.Event,
) -> threading.Thread:
    """Print the 'open this URL' banner — and optionally launch a browser — once
    ``ready_port`` is accepting connections, so it lands after the startup logs."""

    def _run() -> None:
        if not _wait_for_port(connect_host, ready_port, stop=stop, timeout=60.0):
            return
        _print_banner(title=title, open_url=open_url, api_url=api_url)
        if open_browser:
            import webbrowser

            try:
                webbrowser.open(open_url)
            except Exception:  # pragma: no cover - platform dependent
                logger.debug("Could not open a browser at %s", open_url)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _run_prod(
    host: str, port: int, log_level: str, *, strict_port: bool, open_browser: bool
) -> None:
    resolved = _resolve_port(host, port, strict=strict_port)
    connect_host = _loopback(host)
    url = f"http://{connect_host}:{resolved}/"
    stop = threading.Event()
    _announce_when_ready(
        connect_host=connect_host,
        ready_port=resolved,
        title="Precursor",
        open_url=url,
        api_url=None,
        open_browser=open_browser,
        stop=stop,
    )
    try:
        uvicorn.run(
            "precursor.backend.main:app",
            host=host,
            port=resolved,
            log_level=log_level,
            log_config=configure_logging(log_level),
            reload=False,
        )
    finally:
        stop.set()


def _inject_dev_cors(frontend_port: int) -> None:
    """Allow the Vite origin via CORS without a manual PRECURSOR_CORS_ORIGINS.

    The /api proxy is same-origin, so this only matters for direct cross-origin
    calls to the backend — but wiring it up removes a setup footgun. Set in the
    environment before uvicorn starts so the --reload child process inherits it.
    """
    existing = list(get_settings().cors_origins)
    extra = [f"http://localhost:{frontend_port}", f"http://127.0.0.1:{frontend_port}"]
    merged = existing + [origin for origin in extra if origin not in existing]
    os.environ["PRECURSOR_CORS_ORIGINS"] = ",".join(merged)


def _run_dev(
    host: str,
    port: int,
    log_level: str,
    *,
    frontend_port: int | None,
    frontend: bool,
    strict_port: bool,
    open_browser: bool,
) -> None:
    log_config = configure_logging(log_level)
    connect_host = _loopback(host)
    backend_port = _resolve_port(host, port, strict=strict_port)

    vite: subprocess.Popen[bytes] | None = None
    vite_lock = threading.Lock()
    stop = threading.Event()
    threads: list[threading.Thread] = []
    resolved_frontend: int | None = None

    frontend_dir = _repo_root() / "frontend"
    if frontend and not (frontend_dir / "package.json").is_file():
        logger.warning(
            "frontend/ not found — running backend only. "
            "`--dev` with the Vite server needs a source checkout.",
        )
        frontend = False

    if frontend:
        # One knob: the Vite port follows the (resolved) backend port unless the
        # user pinned --frontend-port. Resolve it too so parallel instances and
        # a busy 5173/legacy port never collide.
        preferred_fe = backend_port + 1 if frontend_port is None else frontend_port
        resolved_frontend = _resolve_port(
            host, preferred_fe, strict=strict_port, avoid=frozenset({backend_port})
        )
        _inject_dev_cors(resolved_frontend)

        def _start_vite_when_ready() -> None:
            nonlocal vite
            # Start Vite only once the backend port is listening, so its /api
            # proxy doesn't fire at a backend that's still booting.
            ready = _wait_for_port(connect_host, backend_port, stop=stop)
            if stop.is_set():
                return
            if ready:
                logger.info("Backend ready — starting Vite dev server on :%s", resolved_frontend)
            else:
                logger.warning(
                    "Backend not listening yet; starting Vite dev server on :%s anyway.",
                    resolved_frontend,
                )
            with vite_lock:
                if stop.is_set():
                    return
                vite = subprocess.Popen(
                    [
                        "npm",
                        "--prefix",
                        str(frontend_dir),
                        "run",
                        "dev",
                        "--",
                        "--port",
                        str(resolved_frontend),
                    ],
                    # Let vite.config.ts point its /api proxy at the real backend
                    # port/host (so --port is honoured end-to-end).
                    env={
                        **os.environ,
                        "PRECURSOR_PORT": str(backend_port),
                        "PRECURSOR_HOST": connect_host,
                    },
                )

        vite_thread = threading.Thread(target=_start_vite_when_ready, daemon=True)
        vite_thread.start()
        threads.append(vite_thread)

    ready_port = resolved_frontend if (frontend and resolved_frontend is not None) else backend_port
    # Probe/advertise via "localhost" for the UI so the readiness check matches
    # Vite's IPv6 (::1) bind; the backend-only case stays on the loopback addr.
    ui_host = "localhost" if frontend else connect_host
    open_url = f"http://{ui_host}:{ready_port}/"
    api_url = f"http://{connect_host}:{backend_port}/" if frontend else None
    threads.append(
        _announce_when_ready(
            connect_host=ui_host,
            ready_port=ready_port,
            title="Precursor (dev)",
            open_url=open_url,
            api_url=api_url,
            open_browser=open_browser,
            stop=stop,
        )
    )

    def _terminate(*_: object) -> None:
        stop.set()
        with vite_lock:
            if vite and vite.poll() is None:
                vite.terminate()

    try:
        uvicorn.run(
            "precursor.backend.main:app",
            host=host,
            port=backend_port,
            log_level=log_level,
            log_config=log_config,
            reload=True,
        )
    finally:
        _terminate()
        for thread in threads:
            thread.join(timeout=5)
        if vite is not None:
            try:
                vite.wait(timeout=10)
            except subprocess.TimeoutExpired:
                vite.kill()


def main() -> None:
    cfg = get_settings()
    parser = argparse.ArgumentParser(prog="precursor", description=__doc__)
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Run the development stack (uvicorn --reload + Vite HMR).",
    )
    parser.add_argument("--host", default=cfg.host, help=f"Bind host (default: {cfg.host}).")
    parser.add_argument(
        "--port",
        type=int,
        default=cfg.port,
        help=(
            f"Server/API port (default: {cfg.port}; 0 = pick any free port). A busy "
            "port auto-bumps to the next free one unless --strict-port is given."
        ),
    )
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=None,
        help="Vite dev server port in --dev mode (default: the API port + 1).",
    )
    parser.add_argument(
        "--strict-port",
        action="store_true",
        help="Fail if the requested port is busy instead of auto-selecting another.",
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the app in your default browser once it is ready.",
    )
    parser.add_argument(
        "--no-frontend",
        action="store_true",
        help="In --dev mode, skip the Vite server and run the backend only.",
    )
    parser.add_argument("--log-level", default=cfg.log_level, help="uvicorn log level.")
    args = parser.parse_args()

    if args.dev:
        _run_dev(
            args.host,
            args.port,
            args.log_level,
            frontend_port=args.frontend_port,
            frontend=not args.no_frontend,
            strict_port=args.strict_port,
            open_browser=args.open_browser,
        )
    else:
        _run_prod(
            args.host,
            args.port,
            args.log_level,
            strict_port=args.strict_port,
            open_browser=args.open_browser,
        )


if __name__ == "__main__":
    main()
