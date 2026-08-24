"""GitHub Projects v2 kanban board for Precursor.

Installed as ``precursor-ai[kanban]`` (or on its own), this package registers a
``kanban`` section: a sidebar entry, a top-level ``/kanban`` route and the
``/api/github/projects`` endpoints backing the board.
"""

from __future__ import annotations

from precursor_kanban.plugin import SECTION_ID, register

__all__ = ["SECTION_ID", "register"]
