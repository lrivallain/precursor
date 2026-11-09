"""Shared helpers for the backend test suite.

Reuse these instead of re-declaring a local ``_init_db`` / ``_uid`` / setting
writer / topic factory in each test module. Fixtures that need pytest's
machinery live in ``conftest.py``; plain helpers live here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting


def init_db() -> None:
    """Bring the test database to the latest schema.

    The app's lifespan runs ``init_db`` (``alembic upgrade head``), so opening
    and closing a client is enough before touching tables directly.
    """
    with TestClient(create_app()):
        pass


async def set_app_setting(key: str, value: object) -> None:
    """Store a DB-backed setting (JSON-encoded), creating or replacing it."""
    encoded = json.dumps(value)
    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=encoded))
        else:
            row.value = encoded
        await session.commit()


def unique_id(prefix: str = "t") -> str:
    """A short unique id. Starts with a letter so YAML never reads it as a number."""
    return prefix + uuid.uuid4().hex[:8]


def create_topic(client: TestClient, title: str = "Test topic", **fields: Any) -> dict[str, Any]:
    """Create a topic through the API and return its JSON."""
    res = client.post("/api/topics", json={"title": title, **fields})
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data


def create_chat(client: TestClient, title: str = "Test chat", **fields: Any) -> dict[str, Any]:
    """Create a side chat through the API and return its JSON."""
    res = client.post("/api/chats", json={"title": title, **fields})
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data
