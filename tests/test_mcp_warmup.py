"""Tests for the background MCP warm-up.

The contract the user asked for, in three parts: warming happens *in the
background* (startup and requests never wait on it), *server per server* rather
than as a concurrent burst, and never at the price of an unprompted browser
sign-in.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import ClassVar

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import AppSetting
from precursor.backend.services.mcp.client import MCPServerEntry
from precursor.backend.services.mcp.warmup import MCPWarmUp


def _settings(**overrides: object) -> Settings:
    """Base settings with warm-up switched back on (conftest disables it globally)."""
    base = get_settings().model_dump()
    base["mcp_warmup_enabled"] = True
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _set_enabled(names: dict[str, bool]) -> None:
    await init_db()
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_enabled")
        encoded = json.dumps(names)
        if row is None:
            session.add(AppSetting(key="mcp_enabled", value=encoded))
        else:
            row.value = encoded
        await session.commit()


class _RecordingManager:
    """Manager stand-in that records warm-up decisions without connecting."""

    def __init__(self, entries: list[MCPServerEntry], *, short_circuited: set[str] | None = None):
        self._entries = {e.name: e for e in entries}
        self._short = short_circuited or set()
        self.acquired_names: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0

    def list_entries(self) -> list[MCPServerEntry]:
        return list(self._entries.values())

    def get(self, name: str) -> MCPServerEntry | None:
        return self._entries.get(name)

    def is_auth_short_circuited(self, name: str) -> bool:
        return name in self._short

    def acquired(self, names: list[str], *, github_token: str = ""):  # type: ignore[no-untyped-def]
        manager = self

        class _Ctx:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                manager.acquired_names.extend(names)
                manager.concurrent += 1
                manager.max_concurrent = max(manager.max_concurrent, manager.concurrent)
                await asyncio.sleep(0.02)  # stand in for a real connect
                for name in names:
                    entry = manager._entries[name]
                    entry.state = "ready"
                    entry.tools_from_cache = False

                class _Bundle:
                    unavailable: ClassVar[list[tuple[str, str]]] = []

                return _Bundle()

            async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
                manager.concurrent -= 1
                return False

        return _Ctx()


async def _finish(warmup: MCPWarmUp) -> None:
    """Wait for the sweep to complete instead of sleeping a guessed interval.

    The first test to touch the database also pays the schema migration, which
    is exactly the kind of variable delay a fixed ``asyncio.sleep`` gets wrong.
    """
    task = warmup._task
    assert task is not None
    await asyncio.wait_for(asyncio.shield(task), timeout=10)
    await warmup.stop()


def _entry(name: str, state: str = "disconnected") -> MCPServerEntry:
    entry = MCPServerEntry(name=name, transport="stdio", command="x")
    entry.state = state  # type: ignore[assignment]
    return entry


def _install(monkeypatch, manager: _RecordingManager) -> None:  # type: ignore[no-untyped-def]
    from precursor.backend.services.mcp import client as client_module

    monkeypatch.setattr(client_module, "get_mcp_client_manager", lambda: manager)


async def test_warmup_visits_servers_one_at_a_time(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Sequential by design: several stdio built-ins each spawn ``npx``."""
    await _set_enabled({"alpha": True, "beta": True, "gamma": True, "delta": False})
    manager = _RecordingManager([_entry("alpha"), _entry("beta"), _entry("gamma"), _entry("delta")])
    _install(monkeypatch, manager)

    warmup = MCPWarmUp(_settings(mcp_warmup_delay_seconds=0.0, mcp_warmup_gap_seconds=0.0))
    await warmup.start()
    await _finish(warmup)

    assert manager.acquired_names == ["alpha", "beta", "gamma"]  # disabled one skipped
    assert manager.max_concurrent == 1


async def test_warmup_never_blocks_the_caller(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``start()`` returns immediately — startup must not wait on any connect."""
    await _set_enabled({"alpha": True, "beta": True})
    manager = _RecordingManager([_entry("alpha"), _entry("beta")])
    _install(monkeypatch, manager)

    warmup = MCPWarmUp(_settings(mcp_warmup_delay_seconds=0.0, mcp_warmup_gap_seconds=0.0))
    started = time.perf_counter()
    await warmup.start()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.01
    assert manager.acquired_names == []  # nothing connected yet
    await warmup.stop()


async def test_warmup_skips_servers_needing_an_interactive_sign_in(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A stale credential must never make the app pop a browser on launch."""
    await _set_enabled({"parked": True, "flagged": True, "fine": True})
    manager = _RecordingManager(
        [_entry("parked", state="needs_auth"), _entry("flagged"), _entry("fine")],
        short_circuited={"flagged"},
    )
    _install(monkeypatch, manager)

    warmup = MCPWarmUp(_settings(mcp_warmup_delay_seconds=0.0, mcp_warmup_gap_seconds=0.0))
    await warmup.start()
    await _finish(warmup)

    assert manager.acquired_names == ["fine"]


async def test_warmup_skips_already_connected_servers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await _set_enabled({"live": True, "cached": True})
    live = _entry("live", state="ready")
    cached = _entry("cached", state="ready")
    # "ready" with a catalogue restored from disk proves nothing about the
    # session, so that one still gets warmed.
    cached.tools_from_cache = True
    manager = _RecordingManager([live, cached])
    _install(monkeypatch, manager)

    warmup = MCPWarmUp(_settings(mcp_warmup_delay_seconds=0.0, mcp_warmup_gap_seconds=0.0))
    await warmup.start()
    await _finish(warmup)

    assert manager.acquired_names == ["cached"]


async def test_warmup_can_be_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await _set_enabled({"alpha": True})
    manager = _RecordingManager([_entry("alpha")])
    _install(monkeypatch, manager)

    warmup = MCPWarmUp(_settings(mcp_warmup_enabled=False, mcp_warmup_delay_seconds=0.0))
    await warmup.start()
    assert warmup._task is None  # never even scheduled
    await warmup.stop()

    assert manager.acquired_names == []


async def test_stop_cancels_a_sweep_in_flight(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Shutdown must not hang on (or outlive) a warm-up still walking the list."""
    await _set_enabled({"alpha": True, "beta": True, "gamma": True})
    manager = _RecordingManager([_entry("alpha"), _entry("beta"), _entry("gamma")])
    _install(monkeypatch, manager)

    warmup = MCPWarmUp(_settings(mcp_warmup_delay_seconds=0.0, mcp_warmup_gap_seconds=5.0))
    await warmup.start()
    # Let the first server land, then cancel while the sweep waits out its gap.
    deadline = time.perf_counter() + 10
    while not manager.acquired_names and time.perf_counter() < deadline:
        await asyncio.sleep(0.01)
    await warmup.stop()

    assert warmup._task is None
    assert manager.acquired_names == ["alpha"]  # the gap never elapsed


async def test_warmup_survives_a_failing_server(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """One broken server must not abort the sweep for the rest."""
    await _set_enabled({"broken": True, "ok": True})
    manager = _RecordingManager([_entry("broken"), _entry("ok")])
    original = manager.acquired

    def acquired(names: list[str], *, github_token: str = ""):  # type: ignore[no-untyped-def]
        if names == ["broken"]:
            raise RuntimeError("transport exploded")
        return original(names, github_token=github_token)

    manager.acquired = acquired  # type: ignore[assignment]
    _install(monkeypatch, manager)

    warmup = MCPWarmUp(_settings(mcp_warmup_delay_seconds=0.0, mcp_warmup_gap_seconds=0.0))
    await warmup.start()
    await _finish(warmup)

    assert manager.acquired_names == ["ok"]
