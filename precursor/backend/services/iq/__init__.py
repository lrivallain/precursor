"""Precursor IQ — ranked, cited retrieval over Precursor's own content.

The package mirrors WorkIQ's contract for LLM callers: ``retrieve`` returns
ranked hits plus a grounding Markdown block with ``[^n]`` citations, and ``ask``
turns those into a cited answer. See ``website/features/iq.md``.

The flush hook that queues changed rows for re-indexing (``events.install``)
is installed by ``precursor.backend.db``, so every writer process gets it.
"""

from __future__ import annotations
