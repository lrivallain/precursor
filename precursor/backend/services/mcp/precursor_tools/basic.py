"""General-purpose utility tools (date/time, maths, units, ids, hashing, system info)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from precursor.backend.services.mcp import basic_tools

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import _GATED, _section_enabled, mcp


# --------------------------------------------------------------------------
# basic tools (stateless helpers; read no Precursor data)
# --------------------------------------------------------------------------
def _basic(section_call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return section_call()
    except basic_tools.ToolError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def get_datetime(timezone: str | None = None) -> dict[str, Any]:
    """Get the current date and time.

    Models can't know "now" on their own. ``timezone`` is an IANA name such as
    ``Europe/Paris`` or ``America/New_York``; omit it for this machine's local zone.
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(
        lambda: basic_tools.describe_datetime(
            datetime.now(basic_tools.resolve_zone(timezone)), timezone
        )
    )


@mcp.tool()
async def convert_timezone(
    time: str, to_timezone: str, from_timezone: str | None = None
) -> dict[str, Any]:
    """Convert a date-time between time zones.

    ``time`` is ISO 8601 (``2026-03-01T14:30:00`` or with an offset/``Z``). A time with
    no offset is read in ``from_timezone`` (an IANA name; default: this machine's zone).
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(lambda: basic_tools.convert_timezone(time, to_timezone, from_timezone))


@mcp.tool()
async def date_math(
    start: str,
    end: str | None = None,
    years: int = 0,
    months: int = 0,
    weeks: int = 0,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Add or subtract time from a date, or measure the gap between two dates.

    With ``end``: returns the difference (seconds, days, a d/h/m/s breakdown and the
    number of business days). Without it: shifts ``start`` by the given amounts
    (negative values subtract; month/year shifts clamp to the month's last day).
    Dates are ISO 8601; ones with no offset are read in ``timezone`` (default: local).
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(
        lambda: basic_tools.date_math(
            start, end, years, months, weeks, days, hours, minutes, timezone
        )
    )


@mcp.tool()
async def calculate(expression: str) -> dict[str, Any]:
    """Evaluate an arithmetic expression exactly instead of guessing.

    Supports ``+ - * / // % **`` (``^`` too), parentheses, the constants ``pi``/``e``/``tau``
    and functions like ``sqrt``, ``log``, ``round``, ``min``, ``max``, ``sum``, ``floor``,
    ``ceil``, ``factorial``, ``sin``/``cos``/``tan``. Nothing else is evaluated.
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(lambda: basic_tools.calculate(expression))


@mcp.tool()
async def convert_units(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    """Convert a quantity between units of the same kind.

    Length (mm cm m km in ft yd mi), mass (mg g kg t oz lb), volume (ml l tsp tbsp cup
    floz pt qt gal), time (ms s min h d wk), data (b kb mb gb tb kib mib gib tib),
    speed (m/s km/h mph kn) and temperature (c f k).
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(lambda: basic_tools.convert_units(value, from_unit, to_unit))


@mcp.tool()
async def generate_uuid(version: int = 4, count: int = 1) -> dict[str, Any]:
    """Generate UUIDs: version 4 (random) or 7 (time-ordered, sortable). ``count`` is 1-100."""
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(lambda: basic_tools.generate_uuid(version, count))


@mcp.tool()
async def random_value(
    kind: str = "token_urlsafe",
    length: int = 32,
    minimum: int = 0,
    maximum: int = 100,
    count: int = 1,
) -> dict[str, Any]:
    """Generate cryptographically secure random values.

    ``kind``: ``token_urlsafe`` or ``token_hex`` (``length`` is in bytes) or ``integer``
    (inclusive ``minimum``..``maximum``). ``count`` is 1-100.
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(lambda: basic_tools.random_value(kind, length, minimum, maximum, count))


@mcp.tool()
async def hash_or_encode(operation: str, text: str) -> dict[str, Any]:
    """Hash or encode/decode text.

    ``operation``: ``md5``, ``sha1``, ``sha256``, ``sha512``, ``base64_encode``,
    ``base64_decode``, ``url_encode``, ``url_decode``, ``hex_encode``, ``hex_decode``,
    ``html_escape`` or ``html_unescape``. Hashes are hex digests of the UTF-8 text.
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return _basic(lambda: basic_tools.hash_or_encode(operation, text))


@mcp.tool()
async def count_text(text: str) -> dict[str, Any]:
    """Count characters, words, lines, sentences and paragraphs, with an estimated token count."""
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    return basic_tools.count_text(text)


@mcp.tool()
async def get_system_info() -> dict[str, Any]:
    """Describe the machine Precursor runs on: app version, OS, architecture, Python, locale, time zone.

    Never includes hostnames, usernames, paths or environment variables.
    """
    if not await _section_enabled("tools"):
        return {"error": _GATED.format(section="tools")}
    from precursor import __version__

    return basic_tools.system_info(__version__)
