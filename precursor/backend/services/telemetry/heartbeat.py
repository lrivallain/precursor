"""The daily heartbeat: which build runs where, and roughly how much it is used.

Everything here is coarse on purpose. Counts go out as buckets, features as
on/off, and nothing names a person, a host, a path, a repository or a
conversation. ``preview`` shows the user this exact envelope before they agree.
"""

from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import entry_points
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor import __version__
from precursor.backend.models import AgentRun, Chat, Message, MessageRole, Topic
from precursor.backend.services import updates
from precursor.backend.services.app_settings import (
    resolve_agents_enabled,
    resolve_iq_embeddings_enabled,
    resolve_live_enabled,
    resolve_llm_provider,
    resolve_mcp_enabled,
)
from precursor.backend.services.backup import resolve_backup_enabled
from precursor.backend.services.telemetry.appinsights import Target, envelope

# Bumped when a property is added, renamed or redefined, so the dashboards can
# tell old rows from new ones.
SCHEMA_VERSION = "1"

_BUCKETS: tuple[tuple[int, str], ...] = ((0, "0"), (9, "1-9"), (49, "10-49"), (199, "50-199"))


def bucket(count: int) -> str:
    for ceiling, label in _BUCKETS:
        if count <= ceiling:
            return label
    return "200+"


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _plugin_count() -> int:
    return len(entry_points(group="precursor.plugins"))


async def _count(session: AsyncSession, stmt: Any) -> int:
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _usage(session: AsyncSession) -> dict[str, str]:
    since = datetime.now(UTC) - timedelta(days=1)
    count = func.count()
    return {
        "messages_24h": bucket(
            await _count(
                session,
                select(count)
                .select_from(Message)
                .where(Message.role == MessageRole.USER, Message.created_at >= since),
            )
        ),
        "agent_runs_24h": bucket(
            await _count(
                session, select(count).select_from(AgentRun).where(AgentRun.created_at >= since)
            )
        ),
        "topics": bucket(await _count(session, select(count).select_from(Topic))),
        "chats": bucket(await _count(session, select(count).select_from(Chat))),
    }


async def _features(session: AsyncSession) -> dict[str, str]:
    mcp_enabled = await resolve_mcp_enabled(session)
    return {
        "llm_provider": await resolve_llm_provider(session),
        "agents": _flag(await resolve_agents_enabled(session)),
        "live": _flag(await resolve_live_enabled(session)),
        "iq": _flag(await resolve_iq_embeddings_enabled(session)),
        "backup": _flag(await resolve_backup_enabled(session)),
        "mcp_servers": bucket(sum(mcp_enabled.values())),
        "plugins": str(_plugin_count()),
    }


def build_tags(install_id: str) -> dict[str, str]:
    """Context shared by every envelope: the build and the anonymous install."""
    return {
        "ai.user.id": install_id,
        "ai.application.ver": __version__,
        "ai.cloud.role": "precursor",
        "ai.device.osVersion": platform.system().lower(),
    }


def runtime_properties() -> dict[str, str]:
    return {
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "channel": updates.default_channel(),
        "install_mode": updates.install_mode(),
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


async def build(session: AsyncSession, target: Target, install_id: str) -> dict[str, Any]:
    properties = {
        **runtime_properties(),
        **await _features(session),
        **await _usage(session),
    }
    return envelope(
        target,
        kind="Event",
        base_type="EventData",
        base_data={"name": "heartbeat", "properties": properties},
        tags=build_tags(install_id),
    )
