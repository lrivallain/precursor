"""``precursor`` command-line launcher.

Two modes:

* ``precursor`` — a single uvicorn process serving the JSON API **and** the
  pre-built SPA on one port. No Node.js runtime required; this is what runs
  from an installed wheel (e.g. ``uvx precursor``).
* ``precursor --dev`` — development stack: uvicorn with ``--reload`` plus the
  Vite dev server (HMR), which proxies ``/api`` back to uvicorn, and a live
  VitePress docs server (HMR) that Vite proxies ``/docs`` to. ``--port`` is
  the URL you open (the Vite UI), exactly like normal mode; the backend runs on
  a separate, normally hidden port (``--api-port``, default ``--port`` + 1) and
  the docs on another (``--docs-port``, default ``--port`` + 2). All processes
  are managed together and shut down on Ctrl-C. Requires a source checkout
  (``uv run precursor --dev``).
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TypedDict

import uvicorn

from precursor.backend import banner
from precursor.backend.config import get_settings
from precursor.backend.logging_config import configure_logging

logger = logging.getLogger(__name__)


class _PopenProcessGroupKwargs(TypedDict, total=False):
    creationflags: int
    start_new_session: bool


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


def _probe_socket(family: int) -> socket.socket:
    """A TCP socket configured to bind exactly like uvicorn does.

    uvicorn sets ``SO_REUSEADDR`` before binding its listen socket, so a port
    left in ``TIME_WAIT`` by a just-closed connection (e.g. an SSE chat stream
    torn down on Ctrl-C) is still bindable. Our pre-flight availability checks
    must use the same option, otherwise they report a freshly stopped port as
    "in use" — refusing to restart under ``--strict-port`` or needlessly bumping
    to another port — even though uvicorn would happily bind it.
    """
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock


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
            sock = _probe_socket(family)
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


def _frontend_is_stale(frontend_dir: Path, dist_dir: Path) -> bool:
    """Return True if the built SPA bundle is missing or out of date.

    The bundle is stale when ``dist_dir`` or its ``index.html`` entry point is
    absent, or when any frontend *source* input is newer than the newest file in
    ``dist_dir``. Source inputs are everything under ``frontend/`` except the
    ``node_modules/`` and ``dist/`` trees (dependencies and build outputs, which
    say nothing about whether the app code changed).
    """
    if not dist_dir.is_dir() or not (dist_dir / "index.html").is_file():
        return True

    def _newest_mtime(root: Path, *, skip: frozenset[str] = frozenset()) -> float:
        newest = 0.0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                try:
                    mtime = os.stat(os.path.join(dirpath, name)).st_mtime
                except OSError:  # pragma: no cover - file vanished mid-walk
                    continue
                if mtime > newest:
                    newest = mtime
        return newest

    newest_source = _newest_mtime(frontend_dir, skip=frozenset({"node_modules", "dist"}))
    newest_dist = _newest_mtime(dist_dir)
    return newest_source > newest_dist


def _ensure_frontend_built(*, rebuild_if_stale: bool = False) -> bool:
    """Ensure the frontend is built; build it if necessary.

    Builds when ``frontend/dist`` is missing, or — when ``rebuild_if_stale`` is
    set (the production path) — when the built bundle is out of date relative to
    its sources. Dev callers leave ``rebuild_if_stale`` False because Vite serves
    source live and never serves ``dist``.

    Returns True if the frontend dist exists or was successfully built;
    returns False if npm is unavailable (non-fatal in production).
    """
    repo_root = _repo_root()
    frontend_dir = repo_root / "frontend"
    dist_dir = repo_root / "frontend" / "dist"

    dist_present = dist_dir.is_dir()
    stale = rebuild_if_stale and _frontend_is_stale(frontend_dir, dist_dir)

    if dist_present and not stale:
        logger.info("Frontend dist already built.")
        return True

    if not (frontend_dir / "package.json").is_file():
        # No source checkout (e.g. wheel serving bundled static). Don't fail:
        # serve whatever dist we have, or warn if there's nothing to serve.
        if dist_present:
            logger.info("Frontend dist present; no source checkout to rebuild from.")
            return True
        logger.warning(
            "frontend/ not found or incomplete — cannot build frontend. "
            "Running from a source checkout requires a complete frontend setup."
        )
        return False

    if stale:
        logger.info("Frontend dist is stale (sources changed since last build) — rebuilding.")
    else:
        logger.info("Building frontend...")
    try:
        result = subprocess.run(
            ["npm", "--prefix", str(frontend_dir), "run", "build"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        if result.returncode != 0:
            logger.warning("Frontend build failed: %s", result.stderr)
            return False
        logger.info("Frontend build succeeded.")
        return True
    except FileNotFoundError:
        logger.warning(
            "npm not found — cannot build frontend. "
            "Install Node.js and npm, or pre-build the frontend with `npm --prefix frontend run build`."
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("Frontend build timed out after 5 minutes.")
        return False
    except Exception as exc:  # pragma: no cover - catch-all for unexpected errors
        logger.warning("Frontend build failed: %s", exc)
        return False


def _ensure_website_deps() -> bool:
    """Ensure ``website/node_modules`` exists so the live docs server can run.

    Installs the VitePress dependencies when they're missing (mirroring the
    frontend auto-build). Returns True when the deps are present or were
    installed successfully, False when they're missing and couldn't be installed
    — the caller degrades to disabling live docs, never failing the whole stack.
    """
    website_dir = _repo_root() / "website"
    if not (website_dir / "package.json").is_file():
        return False
    if (website_dir / "node_modules").is_dir():
        return True

    logger.info("website/node_modules missing — installing docs dependencies...")
    try:
        result = subprocess.run(
            ["npm", "--prefix", str(website_dir), "install"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
    except FileNotFoundError:
        logger.warning(
            "npm not found — skipping live docs. Install Node.js and npm, "
            "or run `npm --prefix website install` (or `make sync`) to enable them.",
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("Docs dependency install timed out after 5 minutes — skipping live docs.")
        return False
    except Exception as exc:  # pragma: no cover - catch-all for unexpected errors
        logger.warning("Docs dependency install failed (%s) — skipping live docs.", exc)
        return False

    if result.returncode != 0:
        logger.warning(
            "Docs dependency install failed — skipping live docs. "
            "Run `npm --prefix website install` (or `make sync`) manually.\n%s",
            result.stderr,
        )
        return False
    logger.info("Docs dependencies installed.")
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
        with _probe_socket(family) as sock:
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
    color = banner.use_color(sys.stderr)
    reset = "\033[0m" if color else ""
    bold = "\033[1m" if color else ""
    dim = "\033[2m" if color else ""
    print(file=sys.stderr)
    for art_line in banner.render(color=color):
        print(art_line, file=sys.stderr)
    print(file=sys.stderr)
    subtitle = "dev" if "dev" in title.lower() else "running"
    print(f"  {dim}Precursor is {subtitle}.{reset}", file=sys.stderr)
    print(f"  {bold}\u25b6 Open   {open_url}{reset}", file=sys.stderr)
    if api_url and api_url != open_url:
        print(f"    {dim}API    {api_url}{reset}", file=sys.stderr)
    print(file=sys.stderr)
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
    _ensure_frontend_built(rebuild_if_stale=True)
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
            timeout_graceful_shutdown=get_settings().shutdown_grace_seconds,
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


def _new_process_group_kwargs() -> _PopenProcessGroupKwargs:
    """``Popen`` kwargs that isolate a child in its own process group/session.

    The Vite dev server is launched via ``npm run dev``, which spawns the real
    Vite (esbuild/node) process as a *grandchild*. Putting npm in its own group
    lets us later signal the entire tree at once, and detaches it from the
    terminal's Ctrl-C so Python's teardown is the single authority on its
    lifecycle (no race between the terminal SIGINT and our own terminate).
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_process_tree(proc: subprocess.Popen[bytes], *, timeout: float = 10.0) -> None:
    """Stop ``proc`` and every process in its group, escalating to a hard kill.

    Signalling only ``npm`` leaves the Vite grandchild holding the UI port, so a
    fresh run reports the port as still in use. We signal the whole process group
    instead so npm *and* its children die together and the port is released
    promptly.
    """
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)
    elif sys.platform == "win32":  # pragma: no cover - Windows-specific path
        with contextlib.suppress(OSError):
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        try:
            proc.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            proc.kill()
    else:
        proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=timeout)


def _run_dev(
    host: str,
    port: int,
    log_level: str,
    *,
    api_port: int | None,
    docs_port: int | None,
    frontend: bool,
    docs: bool,
    strict_port: bool,
    open_browser: bool,
) -> None:
    log_config = configure_logging(log_level)
    connect_host = _loopback(host)

    vite: subprocess.Popen[bytes] | None = None
    vite_lock = threading.Lock()
    docs_proc: subprocess.Popen[bytes] | None = None
    docs_lock = threading.Lock()
    stop = threading.Event()
    threads: list[threading.Thread] = []
    ui_port: int | None = None

    frontend_dir = _repo_root() / "frontend"
    if frontend and not (frontend_dir / "package.json").is_file():
        logger.warning(
            "frontend/ not found — running backend only. "
            "`--dev` with the Vite server needs a source checkout.",
        )
        frontend = False

    if frontend:
        # `--port` is the URL you open in the browser — same as prod. In --dev
        # that URL is the Vite dev server (it proxies /api to the backend), so
        # the UI takes `--port` and the backend moves to a separate, normally
        # hidden port (`--api-port`, default the UI port + 1). Both are resolved
        # so parallel instances and a busy port never collide.
        ui_port = _resolve_port(host, port, strict=strict_port)
        preferred_api = api_port if api_port is not None else ui_port + 1
        backend_port = _resolve_port(
            host, preferred_api, strict=strict_port, avoid=frozenset({ui_port})
        )
        _ensure_frontend_built()
        _inject_dev_cors(ui_port)

        # In-app docs with hot reload: run a VitePress dev server (base /docs/)
        # on its own hidden port and let the SPA's Vite proxy /docs to it (see
        # frontend/vite.config.ts, which reads PRECURSOR_DOCS_PORT). Its HMR
        # client is pointed back at the UI port so websocket updates ride through
        # the single origin the user opened. Best-effort: a missing website/ or
        # deps that can't be installed just disables live docs, never the whole
        # stack.
        website_dir = _repo_root() / "website"
        resolved_docs_port: int | None = None
        if docs and not _ensure_website_deps():
            docs = False
        if docs:
            preferred_docs = docs_port if docs_port is not None else ui_port + 2
            resolved_docs_port = _resolve_port(
                host, preferred_docs, strict=strict_port, avoid=frozenset({ui_port, backend_port})
            )
            os.environ["PRECURSOR_DOCS_PORT"] = str(resolved_docs_port)

            def _start_docs() -> None:
                nonlocal docs_proc
                with docs_lock:
                    if stop.is_set():
                        return
                    logger.info("Starting VitePress docs (HMR) on :%s", resolved_docs_port)
                    docs_proc = subprocess.Popen(
                        [
                            "npm",
                            "--prefix",
                            str(website_dir),
                            "run",
                            "docs:dev",
                            "--",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(resolved_docs_port),
                        ],
                        env={
                            **os.environ,
                            # Serve under the same subpath the app mounts docs at.
                            "DOCS_BASE": "/docs/",
                            # Point HMR at the UI origin so websocket reloads ride
                            # through the SPA's /docs proxy (ws: true).
                            "PRECURSOR_DOCS_HMR_PORT": str(ui_port),
                        },
                        **_new_process_group_kwargs(),
                    )

            docs_thread = threading.Thread(target=_start_docs, daemon=True)
            docs_thread.start()
            threads.append(docs_thread)

        def _start_vite_when_ready() -> None:
            nonlocal vite
            # Start Vite only once the backend port is listening, so its /api
            # proxy doesn't fire at a backend that's still booting.
            ready = _wait_for_port(connect_host, backend_port, stop=stop)
            if stop.is_set():
                return
            if ready:
                logger.info("Backend ready — starting Vite dev server on :%s", ui_port)
            else:
                logger.warning(
                    "Backend not listening yet; starting Vite dev server on :%s anyway.",
                    ui_port,
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
                        str(ui_port),
                    ],
                    # Let vite.config.ts point its /api proxy at the real backend
                    # port/host (so --port is honoured end-to-end).
                    env={
                        **os.environ,
                        "PRECURSOR_PORT": str(backend_port),
                        "PRECURSOR_HOST": connect_host,
                    },
                    # Own process group so we can later kill npm *and* the real
                    # Vite child together — otherwise the orphaned Vite keeps the
                    # UI port and the next run reports it as in use.
                    **_new_process_group_kwargs(),
                )

        vite_thread = threading.Thread(target=_start_vite_when_ready, daemon=True)
        vite_thread.start()
        threads.append(vite_thread)
    else:
        # Backend only: `--port` is the backend itself, exactly like prod.
        backend_port = _resolve_port(host, port, strict=strict_port)
        _ensure_frontend_built()

    ready_port = ui_port if (frontend and ui_port is not None) else backend_port
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
            if vite is not None:
                _terminate_process_tree(vite)
        with docs_lock:
            if docs_proc is not None:
                _terminate_process_tree(docs_proc)

    try:
        uvicorn.run(
            "precursor.backend.main:app",
            host=host,
            port=backend_port,
            log_level=log_level,
            log_config=log_config,
            reload=True,
            timeout_graceful_shutdown=get_settings().shutdown_grace_seconds,
        )
    finally:
        _terminate()
        for thread in threads:
            thread.join(timeout=5)


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
            f"Port to open in your browser (default: {cfg.port}; 0 = pick any free "
            "port). Serves the whole app in normal mode, or the dev UI in --dev. A "
            "busy port auto-bumps to the next free one unless --strict-port is given."
        ),
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=None,
        help=(
            "In --dev mode, the backend/API port the Vite UI proxies to "
            "(default: the --port value + 1). You rarely need to set this."
        ),
    )
    parser.add_argument(
        "--docs-port",
        type=int,
        default=None,
        help=(
            "In --dev mode, the port for the live VitePress docs server the UI "
            "proxies /docs to (default: the --port value + 2). Rarely needed."
        ),
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
    parser.add_argument(
        "--no-docs",
        action="store_true",
        help="In --dev mode, skip the live VitePress docs server.",
    )
    parser.add_argument("--log-level", default=cfg.log_level, help="uvicorn log level.")
    args = parser.parse_args()

    if args.dev:
        _run_dev(
            args.host,
            args.port,
            args.log_level,
            api_port=args.api_port,
            docs_port=args.docs_port,
            frontend=not args.no_frontend,
            docs=not args.no_docs,
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
