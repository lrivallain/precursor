"""In-app addresses for Precursor entities, for results read by people.

Shared by the built-in MCP server and Precursor IQ so a caller gets the same
pasteable URL whichever surface produced the hit.
"""

from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.models import Collection, Topic


def app_base_url() -> str:
    """Origin of the running SPA, so a tool result can link to what it changed.

    Uses the configured bind, mapping a wildcard to ``localhost`` because
    ``0.0.0.0`` is not something a browser can open. Best-effort: the value is
    only ever pasted into a human-readable result.
    """
    cfg = get_settings()
    host = {"0.0.0.0": "localhost", "": "localhost", "::": "localhost"}.get(cfg.host, cfg.host)
    authority = f"[{host}]:{cfg.port}" if ":" in host else f"{host}:{cfg.port}"
    return f"http://{authority}"


async def topic_paths(session: AsyncSession) -> dict[int, str]:
    """Root-first slug path (``collection/parent/child``) for every topic.

    Mirrors the readable URL the SPA uses (``/topics/<collection>/…``), so a
    path handed to a caller can be pasted straight into the browser.

    Resolved from a single index load rather than a chain walk per topic, so a
    200-topic ``list_topics`` stays one query instead of 200. Callers used to
    rebuild this by re-fetching each ancestor over MCP.
    """
    rows = (
        await session.execute(select(Topic.id, Topic.slug, Topic.parent_id, Topic.collection_id))
    ).all()
    collection_slugs = {
        cid: slug
        for cid, slug in (await session.execute(select(Collection.id, Collection.slug))).all()
    }
    index: dict[int, tuple[str, int | None]] = {
        tid: (slug, parent_id) for tid, slug, parent_id, _ in rows
    }
    collection_of: dict[int, int | None] = {tid: cid for tid, _, _, cid in rows}
    paths: dict[int, str] = {}
    for tid in index:
        chain: list[str] = []
        # A self-parenting or cyclic row must terminate, not spin forever.
        seen: set[int] = set()
        current: int | None = tid
        while current is not None and current not in seen:
            seen.add(current)
            entry = index.get(current)
            if entry is None:
                break
            slug, parent_id = entry
            chain.append(slug)
            current = parent_id
        chain.reverse()
        cid = collection_of.get(tid)
        prefix = collection_slugs.get(cid) if cid is not None else None
        paths[tid] = "/".join([prefix, *chain]) if prefix else "/".join(chain)
    return paths


def topic_url(path: str) -> str:
    # ``quote`` leaves "/" alone, so the joined segments stay a path.
    return f"{app_base_url()}/topics/{quote(path)}"


def chat_url(slug: str) -> str:
    return f"{app_base_url()}/chats/{quote(slug, safe='')}"


def agent_url(ref: str) -> str:
    return f"{app_base_url()}/agents/{quote(ref, safe='')}"


def live_url(slug: str) -> str:
    return f"{app_base_url()}/live/{quote(slug, safe='')}"
