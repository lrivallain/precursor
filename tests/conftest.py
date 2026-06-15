"""Pytest fixtures + global test isolation.

CRITICAL: point the app at a throwaway SQLite DB *before* anything imports the
backend. ``db.py`` builds its engine at import time from
``get_settings().database_url`` (which otherwise reads ``.env`` → the real
``./precursor.db``), so tests that write settings would pollute the dev
database. Setting the env var here — the first thing pytest loads — guarantees
the cached settings pick up the temp DB instead.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 - kept on disk for the whole test session
    prefix="precursor-test-", suffix=".db", delete=False
)
_tmp.close()
os.environ["PRECURSOR_DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp.name}"


@atexit.register
def _cleanup_tmp_db() -> None:
    with contextlib.suppress(OSError):
        os.unlink(_tmp.name)
