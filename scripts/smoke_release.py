"""Exercise the installed release, never the source checkout or a user's data."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    expected_version = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="precursor-release-") as directory:
        root = Path(directory)
        os.chdir(root)
        for key in ("GITHUB_TOKEN", "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
            os.environ.pop(key, None)
        os.environ.update(
            HOME=directory,
            GH_CONFIG_DIR=str(root / "gh"),
            PATH=str(Path(sys.executable).parent),
            XDG_CONFIG_HOME=str(root / "config"),
            COPILOT_HOME=str(root / "copilot"),
            PRECURSOR_DATABASE_URL=f"sqlite+aiosqlite:///{root / 'smoke.db'}",
            PRECURSOR_DATA_DIR=str(root / "data"),
            PRECURSOR_SKILLS_DIR=str(root / "skills"),
            PRECURSOR_MCP_WARMUP_ENABLED="false",
            PRECURSOR_WORKIQ_KEEPALIVE_ENABLED="false",
        )

        from fastapi.testclient import TestClient

        import precursor
        from precursor.backend.logging_config import configure_logging
        from precursor.backend.main import create_app

        configure_logging("warning")
        if precursor.__version__ != expected_version:
            raise RuntimeError(f"Expected {expected_version}, installed {precursor.__version__}")
        package = Path(precursor.__file__).parent
        for asset in ("frontend_dist/index.html", "website_dist/index.html", "py.typed"):
            if not (package / asset).is_file():
                raise RuntimeError(f"Release wheel is missing {asset}")
        with TestClient(create_app()) as client:
            for route in ("/api/health", "/api/version", "/api/topics", "/", "/docs/"):
                response = client.get(route)
                response.raise_for_status()
            if client.get("/api/version").json()["version"] != expected_version:
                raise RuntimeError("The API reports a different version than the installed wheel.")
        if not (root / "smoke.db").is_file():
            raise RuntimeError("Startup did not create the isolated database.")
        print(
            f"Installed {expected_version}: startup, migrations, API, SPA and docs are available."
        )


if __name__ == "__main__":
    main()
