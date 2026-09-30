"""Answer a question from Precursor's own content, with citations.

Retrieval grounds the answer: the model only sees the numbered excerpts
:func:`~precursor.backend.services.iq.retrieve.retrieve` returns and must cite
them as ``[^n]``. When nothing matches, no model call is made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from precursor.backend.db import SessionLocal
from precursor.backend.services.iq.retrieve import Hit, retrieve

_SYSTEM = """You answer questions using only the user's own Precursor workspace \
(topics, chats, agent runs, live meeting notes, memory) excerpted below.

Rules:
- Ground every claim in the numbered sources and cite them inline as [^n], \
using the source numbers given. Cite each source you rely on.
- If the sources don't contain the answer, say so plainly instead of guessing.
- Be concise; prefer short paragraphs or bullets. Answer in the language of \
the question.
- Today is {today}."""

_NO_MATCH = "I couldn't find anything in Precursor about that."
_CITATION_RE = re.compile(r"\[\^(\d+)\]")


@dataclass(slots=True)
class AskResult:
    question: str
    answer: str
    model: str | None = None
    citations: list[Hit] = field(default_factory=list)
    sources: list[Hit] = field(default_factory=list)


def cited_numbers(answer: str) -> list[int]:
    seen: list[int] = []
    for match in _CITATION_RE.finditer(answer):
        n = int(match.group(1))
        if n not in seen:
            seen.append(n)
    return seen


async def ask(
    question: str,
    *,
    gates: set[str] | None = None,
    containers: set[str] | None = None,
    limit: int = 8,
) -> AskResult:
    """Retrieve, then have the model write a cited answer."""
    from precursor.backend.services.app_settings import resolve_iq_ask_model
    from precursor.backend.services.llm.one_shot import complete_once

    question = (question or "").strip()
    found = await retrieve(question, gates=gates, containers=containers, limit=limit)
    if not found.hits:
        return AskResult(question=question, answer=_NO_MATCH)

    today = datetime.now(UTC).date().isoformat()
    async with SessionLocal() as session:
        model = await resolve_iq_ask_model(session)
        out = await complete_once(
            session,
            system=_SYSTEM.format(today=today),
            user=f"Question: {question}\n\nSources:\n\n{found.markdown}",
            usage_source="/iq-ask",
            model=model or None,
        )
    by_number = {hit.n: hit for hit in found.hits}
    citations = [by_number[n] for n in cited_numbers(out.text) if n in by_number]
    return AskResult(
        question=question,
        answer=out.text.strip(),
        model=out.model,
        citations=citations,
        sources=found.hits,
    )
