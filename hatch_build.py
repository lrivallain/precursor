"""Hatchling build hook: bundle the built SPA and docs when present, conditionally.

The frontend build output (``frontend/dist``) and the VitePress docs output
(``website/.vitepress/dist``) are included in distribution builds so an installed
wheel is self-contained. It's done by a hook rather than a static
``force-include`` because these are only built for real builds:

* **editable installs** (``uv sync`` / dev / CI) don't build them; a static
  force-include would hard-fail on the missing directory. The runtime finds
  them via the source-tree fallback, so we skip inclusion entirely.
* **sdist** keeps each build product at its original path (the wheel is built
  *from* the sdist, so the files must travel along).
* **wheel** maps ``frontend/dist`` → ``precursor/frontend_dist`` and
  ``website/.vitepress/dist`` → ``precursor/website_dist`` so both live inside
  the importable package.

The docs must be built with base ``/docs/`` (``make docs``) so the app can mount
them at ``/docs/``; GitHub Pages builds the same source with base ``/`` in its
own workflow and is unrelated to this bundle.
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
        force_include = build_data.setdefault("force_include", {})
        # (source dir, sdist dest, wheel dest) for each optional build product.
        for src_parts, sdist_dest, wheel_dest in (
            (("frontend", "dist"), "frontend/dist", "precursor/frontend_dist"),
            (
                ("website", ".vitepress", "dist"),
                "website/.vitepress/dist",
                "precursor/website_dist",
            ),
        ):
            src = os.path.join(self.root, *src_parts)
            if not os.path.isdir(src):
                # Not built (e.g. a plain build without the npm build step).
                # Skip rather than fail; the artifact just omits that asset.
                continue
            force_include[src] = sdist_dest if self.target_name == "sdist" else wheel_dest
