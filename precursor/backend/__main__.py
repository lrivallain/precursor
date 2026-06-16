"""``precursor`` command-line launcher.

Two modes:

* ``precursor`` — a single uvicorn process serving the JSON API **and** the
  pre-built SPA on one port. No Node.js runtime required; this is what runs
  from an installed wheel (e.g. ``uvx precursor``).
* ``precursor --dev`` — development stack: uvicorn with ``--reload`` plus the
  Vite dev server (HMR) on :5173, which proxies ``/api`` back to uvicorn. Both
  processes are managed together and shut down on Ctrl-C. Requires a source
  checkout (``uv run precursor --dev``).
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

import uvicorn

from precursor.backend.config import get_settings
from precursor.backend.logging_config import configure_logging

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_prod(host: str, port: int, log_level: str) -> None:
    uvicorn.run(
        "precursor.backend.main:app",
        host=host,
        port=port,
        log_level=log_level,
        log_config=configure_logging(log_level),
        reload=False,
    )


def _run_dev(host: str, port: int, log_level: str, *, frontend_port: int, frontend: bool) -> None:
    log_config = configure_logging(log_level)
    vite: subprocess.Popen[bytes] | None = None
    if frontend:
        frontend_dir = _repo_root() / "frontend"
        if not (frontend_dir / "package.json").is_file():
            logger.warning(
                "frontend/ not found — running backend only. "
                "`--dev` with the Vite server needs a source checkout.",
            )
        else:
            logger.info("Starting Vite dev server on :%s", frontend_port)
            vite = subprocess.Popen(
                [
                    "npm",
                    "--prefix",
                    str(frontend_dir),
                    "run",
                    "dev",
                    "--",
                    "--port",
                    str(frontend_port),
                ],
            )

    def _terminate(*_: object) -> None:
        if vite and vite.poll() is None:
            vite.terminate()

    try:
        uvicorn.run(
            "precursor.backend.main:app",
            host=host,
            port=port,
            log_level=log_level,
            log_config=log_config,
            reload=True,
        )
    finally:
        _terminate()
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
        help="Run the development stack (uvicorn --reload + Vite HMR on :5173).",
    )
    parser.add_argument("--host", default=cfg.host, help=f"Bind host (default: {cfg.host}).")
    parser.add_argument(
        "--port", type=int, default=cfg.port, help=f"API port (default: {cfg.port})."
    )
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=5173,
        help="Vite dev server port in --dev mode (default: 5173).",
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
        )
    else:
        _run_prod(args.host, args.port, args.log_level)


if __name__ == "__main__":
    main()
