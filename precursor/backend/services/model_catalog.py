"""Cached view of the model catalogue the active LLM provider advertises.

The chat model is a plain string handed straight to the provider's API, so an
id the provider has since retired fails *every* turn — which is exactly what a
pinned factory default eventually becomes. Instead of shipping a literal that
rots, callers resolve against what the provider offers right now.

The catalogue is a network round-trip, so it is cached per provider behind a
lock (so a burst of turns triggers one fetch, not N), and a failure degrades to
"unknown" rather than an error: a turn must never be blocked because we
couldn't reach the catalogue.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Catalogues change on the provider's release cadence, not ours, so a few
# minutes of staleness costs nothing and keeps this off the per-turn hot path.
CATALOG_TTL_SECONDS = 300.0

# provider id -> (fetched_at monotonic, model ids in catalogue order)
_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
_locks: dict[str, asyncio.Lock] = {}


def invalidate_model_catalog(provider: str | None = None) -> None:
    """Drop cached catalogues so the next read re-fetches.

    Called when credentials or the selected provider change — until then a
    tokenless GitHub Copilot config resolves to the mock provider, and its
    one-entry catalogue must not outlive the token being added.
    """
    if provider is None:
        _cache.clear()
    else:
        _cache.pop(provider, None)


async def offered_model_ids(session: AsyncSession) -> tuple[str, ...] | None:
    """Model ids the active provider currently offers, in catalogue order.

    Returns ``None`` when the catalogue can't be determined — the provider
    doesn't publish one, or the fetch failed. Callers must then trust whatever
    is configured instead of second-guessing it.
    """
    from precursor.backend.services.app_settings import resolve_llm_provider

    provider_id = await resolve_llm_provider(session)
    cached = _cache.get(provider_id)
    if cached is not None and (time.monotonic() - cached[0]) < CATALOG_TTL_SECONDS:
        return cached[1]

    lock = _locks.setdefault(provider_id, asyncio.Lock())
    async with lock:
        # A waiter that queued behind the fetch shouldn't repeat it.
        cached = _cache.get(provider_id)
        if cached is not None and (time.monotonic() - cached[0]) < CATALOG_TTL_SECONDS:
            return cached[1]
        return await _fetch(session, provider_id)


async def _fetch(session: AsyncSession, provider_id: str) -> tuple[str, ...] | None:
    from precursor.backend.services.llm import get_llm_provider

    provider = await get_llm_provider(session)
    lister = getattr(provider, "list_models", None)
    if lister is None:
        return None
    try:
        models = await lister()
    except Exception as exc:  # network / auth failures must not break a turn
        logger.warning("Model catalogue fetch failed for %s: %s", provider_id, exc)
        return None
    ids = tuple(m.id for m in models if getattr(m, "id", ""))
    if not ids:
        return None
    _cache[provider_id] = (time.monotonic(), ids)
    return ids
