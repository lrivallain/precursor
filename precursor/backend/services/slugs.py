"""Topic and Chat slug generation and uniqueness."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Topic

if TYPE_CHECKING:
    from precursor.backend.models import Chat

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_LEN = 80


def slugify(text: str) -> str:
    """Return an ASCII, lowercase, hyphenated slug fragment for `text`.

    Strips diacritics so French/accented titles still produce readable slugs.
    Returns an empty string when no alnum content survives.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    s = _SLUG_RE.sub("-", ascii_only.lower()).strip("-")
    return s[:_MAX_LEN]


async def allocate_unique_slug[T: (Topic, "Chat")](
    session: AsyncSession,
    base: str,
    model: type[T],
    *,
    exclude_id: int | None = None,
) -> str:
    """Return a slug equal to `base`, or `base-2`, `base-3`, … if taken.

    `exclude_id` lets the model keep its current slug during an update.
    """
    if not base:
        base = "item"
    candidate = base
    n = 2
    while True:
        stmt = select(model.id).where(model.slug == candidate)
        if exclude_id is not None:
            stmt = stmt.where(model.id != exclude_id)
        existing = (await session.execute(stmt)).first()
        if existing is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1
