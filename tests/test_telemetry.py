"""Anonymous usage data: what is sent, when, and what never is."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.services.telemetry import appinsights, errors, heartbeat, prefs
from precursor.backend.services.telemetry.ticker import TelemetryTicker
from tests.support import set_app_setting

CONNECTION = (
    "InstrumentationKey=11111111-2222-3333-4444-555555555555;"
    "IngestionEndpoint=https://westeurope-5.in.applicationinsights.azure.com/;"
    "LiveEndpoint=https://westeurope.livediagnostics.monitor.azure.com/"
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A configured endpoint, no opt-out in the environment, and a clean slate."""
    for var in ("CI", "DO_NOT_TRACK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(get_settings(), "telemetry_connection_string", CONNECTION)
    monkeypatch.setattr(get_settings(), "telemetry", "")
    # A fresh collector: the daily dedupe would otherwise carry across tests.
    monkeypatch.setattr(errors, "collector", errors._Collector())
    yield
    path = os.environ["PRECURSOR_DATABASE_URL"].split(":///", 1)[1]
    con = sqlite3.connect(path, timeout=10)
    try:
        con.execute("DELETE FROM app_settings WHERE key LIKE 'telemetry_%'")
        con.commit()
    except sqlite3.OperationalError:  # not migrated yet
        pass
    finally:
        con.close()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []

    async def fake_post(_target: appinsights.Target, envelopes: list[dict[str, Any]]) -> bool:
        if envelopes:
            batches.append(envelopes)
        return True

    monkeypatch.setattr(appinsights, "post", fake_post)
    return batches


def test_connection_string_gives_key_and_track_url() -> None:
    target = appinsights.parse_connection_string(CONNECTION)
    assert target is not None
    assert target.instrumentation_key == "11111111-2222-3333-4444-555555555555"
    assert target.track_url == "https://westeurope-5.in.applicationinsights.azure.com/v2/track"


@pytest.mark.parametrize(
    "raw", ["", "IngestionEndpoint=https://x/", "InstrumentationKey=k;IngestionEndpoint=http://x/"]
)
def test_unusable_connection_strings_are_rejected(raw: str) -> None:
    assert appinsights.parse_connection_string(raw) is None


@pytest.mark.parametrize(
    ("env", "value", "reason"),
    [
        ("DO_NOT_TRACK", "1", "DO_NOT_TRACK"),
        ("CI", "true", "CI"),
    ],
)
def test_environment_opt_outs_block_sending(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, reason: str
) -> None:
    assert prefs.blocked_reason() is None
    monkeypatch.setenv(env, value)
    assert reason in (prefs.blocked_reason() or "")


def test_precursor_telemetry_off_and_missing_endpoint_block_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "telemetry", "off")
    assert "PRECURSOR_TELEMETRY" in (prefs.blocked_reason() or "")
    monkeypatch.setattr(get_settings(), "telemetry", "")
    monkeypatch.setattr(get_settings(), "telemetry_connection_string", "")
    assert "no telemetry endpoint" in (prefs.blocked_reason() or "")


@pytest.mark.parametrize(
    ("count", "label"), [(0, "0"), (1, "1-9"), (10, "10-49"), (199, "50-199"), (5000, "200+")]
)
def test_counts_go_out_as_buckets(count: int, label: str) -> None:
    assert heartbeat.bucket(count) == label


async def test_heartbeat_envelope_is_coarse_and_anonymous() -> None:
    with TestClient(create_app()):
        pass
    target = prefs.target()
    assert target is not None
    async with SessionLocal() as session:
        item = await heartbeat.build(session, target, "install-1")
    assert item["name"] == "Microsoft.ApplicationInsights.Event"
    assert item["tags"]["ai.user.id"] == "install-1"
    assert item["tags"]["ai.internal.sdkVersion"] == appinsights.SDK_VERSION
    props = item["data"]["baseData"]["properties"]
    assert item["data"]["baseData"]["name"] == "heartbeat"
    assert {"version", "os", "install_mode", "messages_24h", "agents"} <= props.keys()
    assert all(isinstance(v, str) for v in props.values())
    # Nothing identifying: no host name, user name or home path anywhere.
    body = json.dumps(item)
    assert os.path.expanduser("~") not in body
    assert socket.gethostname() not in body


async def test_nothing_is_sent_before_the_user_says_yes(sent: list[Any]) -> None:
    with TestClient(create_app()):
        pass
    await TelemetryTicker().run_once()
    assert sent == []
    async with SessionLocal() as session:
        assert (await prefs.load_choice(session)).decided is False


async def test_heartbeat_goes_once_a_day_after_opt_in(sent: list[Any]) -> None:
    with TestClient(create_app()):
        pass
    await set_app_setting(prefs.ENABLED_KEY, True)
    ticker = TelemetryTicker()
    await ticker.run_once()
    await ticker.run_once()
    assert len(sent) == 1
    assert sent[0][0]["data"]["baseData"]["name"] == "heartbeat"


async def test_saying_no_forgets_the_install_id(sent: list[Any]) -> None:
    with TestClient(create_app()):
        pass
    await set_app_setting(prefs.ENABLED_KEY, True)
    await TelemetryTicker().run_once()
    first = sent[0][0]["tags"]["ai.user.id"]
    await set_app_setting(prefs.ENABLED_KEY, False)
    await TelemetryTicker().run_once()
    async with SessionLocal() as session:
        assert await prefs.last_heartbeat(session) is None
    await set_app_setting(prefs.ENABLED_KEY, True)
    await TelemetryTicker().run_once()
    assert sent[-1][0]["tags"]["ai.user.id"] != first


def _record_with_exception(message: str) -> logging.LogRecord:
    try:
        raise ValueError(message)
    except ValueError:
        return logging.LogRecord(
            "precursor.backend.services.demo",
            logging.ERROR,
            __file__,
            1,
            message,
            None,
            sys.exc_info(),
        )


def test_error_report_keeps_type_and_own_frames_never_the_message() -> None:
    record = _record_with_exception("secret prompt /Users/alice/notes.md")
    report = errors.report_from(record)
    assert report is not None
    assert report.type_name == "ValueError"
    # The raising frame is in tests/, outside the package: no frame is kept.
    assert report.frames == ()
    target = prefs.target()
    assert target is not None
    body = json.dumps(errors.build(target, report, heartbeat.build_tags("i")))
    assert "secret" not in body
    assert "alice" not in body


def test_error_reports_are_deduplicated_and_capped() -> None:
    errors.collector.set_enabled(True)
    report = errors.report_from(_record_with_exception("x"))
    assert report is not None
    errors.collector.add(report)
    errors.collector.add(report)
    assert len(errors.collector.drain()) == 1
    errors.collector.add(report)
    assert errors.collector.drain() == []
    for n in range(errors.MAX_REPORTS_PER_DAY + 5):
        errors.collector.add(errors.ErrorReport(f"fp{n}", "E", "l", ()))
    assert len(errors.collector.drain()) == errors.MAX_REPORTS_PER_DAY - 1


async def test_logged_errors_are_sent_only_with_error_reports_on(sent: list[Any]) -> None:
    with TestClient(create_app()):
        pass
    errors.install_handler()
    try:
        await set_app_setting(prefs.ENABLED_KEY, True)
        ticker = TelemetryTicker()
        await ticker.run_once()
        logging.getLogger("precursor.backend.demo").exception(
            "boom", exc_info=_record_with_exception("a").exc_info
        )
        await set_app_setting(prefs.ERRORS_KEY, True)
        await ticker.run_once()
        logging.getLogger("precursor.backend.demo").exception(
            "boom", exc_info=_record_with_exception("b").exc_info
        )
        await ticker.run_once()
    finally:
        errors.uninstall_handler()
    kinds = [item["name"].rsplit(".", 1)[1] for batch in sent for item in batch]
    assert kinds == ["Event", "Exception"]


def test_status_and_settings_round_trip(sent: list[Any]) -> None:
    with TestClient(create_app()) as client:
        status = client.get("/api/telemetry").json()
        assert status["available"] is True
        assert status["decided"] is False
        assert '"heartbeat"' in status["preview"]
        response = client.put("/api/settings", json={"telemetry_enabled": False})
        assert response.json()["telemetry_enabled"] is False
        status = client.get("/api/telemetry").json()
        assert status["decided"] is True
        assert status["enabled"] is False
