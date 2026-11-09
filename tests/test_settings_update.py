"""Saving settings: what each changed key sets off, and how partial updates merge."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import backup_ticker
from precursor.backend.services.agents.manager import get_agent_manager
from precursor.backend.services.iq import ticker as iq_ticker
from precursor.backend.services.mcp import agent365
from precursor.backend.services.mcp import client as mcp_client


@pytest.fixture(autouse=True)
def _restore_settings() -> Iterator[None]:
    """Put the shared scratch DB's settings back: later tests must not inherit these."""
    path = os.environ["PRECURSOR_DATABASE_URL"].split(":///", 1)[1]

    def rows() -> list[tuple[Any, ...]]:
        con = sqlite3.connect(path, timeout=10)
        try:
            return con.execute("SELECT * FROM app_settings").fetchall()
        except sqlite3.OperationalError:  # not migrated yet
            return []
        finally:
            con.close()

    saved = rows()
    yield
    con = sqlite3.connect(path, timeout=10)
    try:
        con.execute("DELETE FROM app_settings")
        if saved:
            marks = ", ".join("?" * len(saved[0]))
            con.executemany(f"INSERT INTO app_settings VALUES ({marks})", saved)
        con.commit()
    finally:
        con.close()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failing: set[str] = set()

    def hook(self, name: str) -> Any:
        async def record(*_args: Any, **_kwargs: Any) -> None:
            self.calls.append(name)
            if name in self.failing:
                raise RuntimeError(f"{name} broke")

        return record


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    manager = get_agent_manager()
    monkeypatch.setattr(manager, "start", rec.hook("agents.start"))
    monkeypatch.setattr(manager, "stop", rec.hook("agents.stop"))
    monkeypatch.setattr(manager, "apply_session_overrides", rec.hook("agents.overrides"))
    monkeypatch.setattr(backup_ticker.get_backup_ticker(), "nudge", rec.hook("backup.nudge"))
    monkeypatch.setattr(iq_ticker.get_iq_ticker(), "nudge", rec.hook("iq.nudge"))
    monkeypatch.setattr(agent365, "configure_agent365_servers", rec.hook("agent365"))
    monkeypatch.setattr(mcp_client, "configure_playwright_server", rec.hook("playwright"))
    mcp = mcp_client.get_mcp_client_manager()
    monkeypatch.setattr(mcp, "retire_worker", rec.hook("mcp.retire"))
    monkeypatch.setattr(mcp, "probe", rec.hook("mcp.probe"))
    return rec


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"theme": "dark"}, []),
        ({"agents_enabled": True}, ["agents.start"]),
        ({"agents_enabled": False}, ["agents.stop"]),
        ({"agents_reasoning_effort": "high"}, ["agents.overrides"]),
        ({"agents_default_model": "gpt-x"}, ["agents.overrides"]),
        ({"backup_enabled": False}, ["backup.nudge"]),
        ({"backup_dir": ""}, ["backup.nudge"]),
        ({"iq_embeddings_enabled": True}, ["iq.nudge"]),
        ({"iq_embeddings_enabled": False}, []),
        ({"iq_embedding_model": "m"}, ["iq.nudge"]),
        ({"workiq_tenant_id": "tenant"}, ["agent365"]),
        ({"mcp_expose": {"topics": True}}, ["mcp.retire"]),
        ({"playwright_browser": "chrome"}, ["playwright"]),
        (
            {"agents_enabled": True, "agents_context_tier": "max", "playwright_browser": "chrome"},
            ["agents.start", "agents.overrides", "playwright"],
        ),
    ],
)
def test_each_changed_setting_reaches_its_component(
    recorder: _Recorder, payload: dict[str, Any], expected: list[str]
) -> None:
    with TestClient(create_app()) as client:
        recorder.calls.clear()
        response = client.put("/api/settings", json=payload)
        assert response.status_code == 200, response.text
        assert [c for c in recorder.calls if c != "mcp.probe"] == expected


def test_a_failing_reaction_is_logged_and_the_rest_still_run(
    recorder: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    with TestClient(create_app()) as client:
        recorder.calls.clear()
        recorder.failing = {"agents.start", "iq.nudge"}
        # The app's logging config stops ``precursor`` records at its own logger.
        app_logger = logging.getLogger("precursor")
        app_logger.addHandler(caplog.handler)
        try:
            response = client.put(
                "/api/settings",
                json={"agents_enabled": True, "iq_embeddings_enabled": True, "theme": "dark"},
            )
        finally:
            app_logger.removeHandler(caplog.handler)
        assert response.status_code == 200
        assert response.json()["theme"] == "dark"
        assert recorder.calls == ["agents.start", "iq.nudge"]
    messages = [r.getMessage() for r in caplog.records]
    assert "Agents runtime reconcile failed after settings update" in messages
    assert "Precursor IQ nudge failed after settings update" in messages


def test_unknown_iq_provider_is_refused() -> None:
    with TestClient(create_app()) as client:
        response = client.put("/api/settings", json={"iq_ask_provider": "nope"})
        assert response.status_code == 422
        assert response.json()["detail"] == "Unknown LLM provider for iq_ask_provider."
        assert client.put("/api/settings", json={"iq_ask_provider": ""}).status_code == 200


def test_api_keys_merge_and_an_empty_value_removes_one() -> None:
    with TestClient(create_app()) as client:
        client.put("/api/settings", json={"api_keys": {"test_alpha": "a"}})
        present = client.put("/api/settings", json={"api_keys": {"test_beta": "b"}}).json()
        assert present["api_keys_present"]["test_alpha"] is True
        assert present["api_keys_present"]["test_beta"] is True
        present = client.put("/api/settings", json={"api_keys": {"test_alpha": ""}}).json()
        assert "test_alpha" not in present["api_keys_present"]
        assert present["api_keys_present"]["test_beta"] is True


def test_llm_providers_merge_per_field_and_an_empty_value_clears_it() -> None:
    with TestClient(create_app()) as client:
        client.put(
            "/api/settings",
            json={
                "llm_providers": {
                    "azure_foundry": {"endpoint": "https://e", "deployment": "d", "key": "k"}
                }
            },
        )
        body = client.put(
            "/api/settings", json={"llm_providers": {"azure_foundry": {"deployment": ""}}}
        ).json()
        assert body["llm_providers"]["azure_foundry"] == {"endpoint": "https://e"}
        assert body["llm_providers_present"]["azure_foundry"] == {"key": True}
