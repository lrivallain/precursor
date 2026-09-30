"""Precursor IQ — ranked, cited retrieval over Precursor's own content.

The package mirrors WorkIQ's contract for LLM callers: ``retrieve`` returns
ranked hits plus a grounding Markdown block with ``[^n]`` citations, and ``ask``
turns those into a cited answer. See ``website/features/iq.md``.

Importing the package installs the flush hook that queues changed rows for
re-indexing (``events.install``).
"""

from __future__ import annotations

from precursor.backend.services.iq import events

events.install()
