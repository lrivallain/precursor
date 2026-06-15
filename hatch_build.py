"""Hatchling build hook: bundle the built SPA when present, conditionally.

The frontend build output (``frontend/dist``) is included in distribution
builds so an installed wheel is self-contained. It's done by a hook rather than
a static ``force-include`` because the SPA is only built for real builds:

* **editable installs** (``uv sync`` / dev / CI) don't build the frontend; a
  static force-include would hard-fail on the missing directory. The runtime
  finds the SPA via the source-tree fallback, so we skip inclusion entirely.
* **sdist** keeps ``frontend/dist`` at its original path (the wheel is built
  *from* the sdist, so the files must travel along).
* **wheel** maps ``frontend/dist`` → ``precursor/frontend_dist`` so the SPA
  lives inside the importable package.
"""

from __future__ import annotations

import os
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBundleHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Editable installs run from the source tree; nothing to bundle.
        if version == "editable":
            return
        dist = os.path.join(self.root, "frontend", "dist")
        if not os.path.isdir(dist):
            # No built frontend (e.g. a plain build without `npm run build`).
            # Skip rather than fail; the resulting artifact just omits the SPA.
            return
        dest = "frontend/dist" if self.target_name == "sdist" else "precursor/frontend_dist"
        build_data.setdefault("force_include", {})[dist] = dest
