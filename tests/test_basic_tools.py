"""The stateless helpers behind the ``tools`` MCP section."""

from __future__ import annotations

import hashlib
import uuid

import pytest

from precursor.backend.services.mcp import basic_tools as bt
from precursor.backend.services.mcp import precursor_server as ps
from precursor.backend.services.mcp.server import TOOL_SECTIONS
from tests.test_precursor_workspace_tools import _reset_expose, _set_expose  # noqa: F401


def test_convert_timezone_uses_the_source_zone_for_naive_times() -> None:
    out = bt.convert_timezone("2026-07-01T12:00:00", "America/New_York", "Europe/Paris")
    assert out["to"]["iso"] == "2026-07-01T06:00:00-04:00"
    assert out["from"]["utc_offset"] == "+0200"
    assert bt.convert_timezone("2026-01-01T00:00:00Z", "Asia/Tokyo")["to"]["time"] == "09:00:00"
    with pytest.raises(bt.ToolError):
        bt.convert_timezone("not a date", "UTC")
    with pytest.raises(bt.ToolError):
        bt.convert_timezone("2026-01-01", "Mars/Olympus")


def test_date_math_shifts_and_clamps_months() -> None:
    out = bt.date_math("2026-01-31", months=1, timezone="UTC")
    assert out["date"] == "2026-02-28"
    assert bt.date_math("2026-03-01", days=-1, timezone="UTC")["date"] == "2026-02-28"
    assert bt.date_math("2026-03-01", years=1, weeks=1, timezone="UTC")["date"] == "2027-03-08"


def test_date_math_measures_the_gap_in_business_days() -> None:
    out = bt.date_math("2026-03-02", "2026-03-09", timezone="UTC")  # Mon -> next Mon
    assert out["total_days"] == 7
    assert out["business_days"] == 5
    assert out["breakdown"] == "7d 0h 0m 0s"
    back = bt.date_math("2026-03-09", "2026-03-02", timezone="UTC")
    assert back["total_days"] == -7
    assert back["business_days"] == -5


def test_calculate_evaluates_safely() -> None:
    assert bt.calculate("2 + 3 * 4")["result"] == 14
    assert bt.calculate("2^10")["result"] == 1024
    assert bt.calculate("sqrt(16) + round(pi, 2)")["result"] == pytest.approx(7.14)
    assert bt.calculate("7 / 2")["result"] == 3.5
    for bad in ("__import__('os').system('x')", "open('f')", "a + 1", "1 +", "2 ** 100000", "1/0"):
        with pytest.raises(bt.ToolError):
            bt.calculate(bad)


def test_convert_units() -> None:
    assert bt.convert_units(1, "mi", "km")["result"] == pytest.approx(1.609344)
    assert bt.convert_units(100, "c", "f")["result"] == pytest.approx(212)
    assert bt.convert_units(1, "GiB", "mb")["result"] == pytest.approx(1073.741824)
    assert bt.convert_units(2, "hours", "min")["result"] == 120
    with pytest.raises(bt.ToolError):
        bt.convert_units(1, "kg", "m")
    with pytest.raises(bt.ToolError):
        bt.convert_units(1, "parsec", "m")


def test_uuids_and_random_values() -> None:
    v4 = bt.generate_uuid(4, 3)["uuids"]
    assert len(set(v4)) == 3 and all(uuid.UUID(u).version == 4 for u in v4)
    v7 = [uuid.UUID(u) for u in bt.generate_uuid(7, 5)["uuids"]]
    assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in v7)
    with pytest.raises(bt.ToolError):
        bt.generate_uuid(1)
    with pytest.raises(bt.ToolError):
        bt.generate_uuid(4, 101)

    ints = bt.random_value("integer", minimum=5, maximum=7, count=50)["values"]
    assert set(ints) <= {5, 6, 7}
    assert len(bt.random_value("token_hex", length=8)["values"][0]) == 16
    with pytest.raises(bt.ToolError):
        bt.random_value("integer", minimum=3, maximum=1)
    with pytest.raises(bt.ToolError):
        bt.random_value("nonsense")


def test_hash_or_encode_round_trips() -> None:
    assert bt.hash_or_encode("sha256", "abc")["result"] == hashlib.sha256(b"abc").hexdigest()
    for enc, dec in (
        ("base64_encode", "base64_decode"),
        ("url_encode", "url_decode"),
        ("hex_encode", "hex_decode"),
        ("html_escape", "html_unescape"),
    ):
        encoded = bt.hash_or_encode(enc, "héllo <b>&</b> ñ/?")["result"]
        assert bt.hash_or_encode(dec, encoded)["result"] == "héllo <b>&</b> ñ/?"
    assert bt.hash_or_encode("base64_decode", "aGk")["result"] == "hi"  # missing padding
    with pytest.raises(bt.ToolError):
        bt.hash_or_encode("hex_decode", "zz")
    with pytest.raises(bt.ToolError):
        bt.hash_or_encode("rot13", "x")


def test_count_text_and_system_info() -> None:
    out = bt.count_text("Hello world. It's fine!\n\nNew paragraph")
    assert out["words"] == 6
    assert out["sentences"] == 3
    assert out["paragraphs"] == 2
    assert out["lines"] == 3
    assert bt.count_text("")["words"] == 0
    info = bt.system_info("1.2.3")
    assert info["app_version"] == "1.2.3"
    assert not {"hostname", "user", "username", "path"} & set(info)


async def test_every_basic_tool_is_gated_then_works() -> None:
    names = [n for n, section in TOOL_SECTIONS.items() if section == "tools"]
    assert len(names) == 10
    calls = {
        "get_datetime": lambda: ps.get_datetime("UTC"),
        "convert_timezone": lambda: ps.convert_timezone("2026-01-01T00:00:00", "UTC", "UTC"),
        "date_math": lambda: ps.date_math("2026-01-01", days=1, timezone="UTC"),
        "calculate": lambda: ps.calculate("1+1"),
        "convert_units": lambda: ps.convert_units(1, "km", "m"),
        "generate_uuid": lambda: ps.generate_uuid(),
        "random_value": lambda: ps.random_value(),
        "hash_or_encode": lambda: ps.hash_or_encode("sha1", "x"),
        "count_text": lambda: ps.count_text("a b"),
        "get_system_info": lambda: ps.get_system_info(),
    }
    assert set(calls) == set(names)

    await _set_expose({})
    for name, call in calls.items():
        assert "not exposed" in (await call())["error"], name
    await _set_expose({"tools": True})
    for name, call in calls.items():
        assert "error" not in await call(), name
    assert (await ps.calculate("1/0"))["error"] == "Division by zero."
