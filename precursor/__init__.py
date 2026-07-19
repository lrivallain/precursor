"""Precursor — per-topic AI chat for work follow-up."""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# PyPI distribution name (renamed from "precursor" in v2026.7.0). This differs
# from the import package name, which intentionally stays "precursor".
_DIST_NAME = "precursor-ai"


def _resolve_version() -> str:
    """Best-effort version: installed metadata → built _version.py → unknown.

    The version is CalVer (YYYY.M.MICRO), derived from git tags by hatch-vcs at
    build/install time (see pyproject ``[tool.hatch.version]``).
    """
    try:
        return _pkg_version(_DIST_NAME)
    except PackageNotFoundError:
        pass
    try:
        # Imported dynamically: this file is generated at build time and absent
        # from a raw source checkout, so a static import would not resolve.
        module = importlib.import_module("precursor._version")
        return str(module.__version__)
    except Exception:  # running from a raw source tree without a built version
        return "0.0.0+unknown"


__version__ = _resolve_version()
