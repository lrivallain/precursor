"""Pure helpers behind the ``tools`` MCP section.

Everything here is stateless and touches no Precursor data, so the MCP tools in
``precursor_server`` stay thin wrappers that only add the section gate.
"""

from __future__ import annotations

import ast
import base64
import binascii
import calendar
import hashlib
import html
import math
import operator
import platform
import re
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ToolError(ValueError):
    """A user-facing problem with a tool's input."""


# -- time ------------------------------------------------------------------
def resolve_zone(name: str | None) -> tzinfo:
    if not name:
        local = datetime.now().astimezone().tzinfo
        assert local is not None
        return local
    if name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ToolError(
            f"Unknown timezone: {name!r}. Use an IANA name like 'Europe/Paris'."
        ) from None


def parse_datetime(value: str, zone: tzinfo | None = None) -> datetime:
    """Parse an ISO 8601 date or datetime; naive values are read in ``zone``."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ToolError(
            f"Could not parse {value!r}. Use ISO 8601, e.g. '2026-03-01' or '2026-03-01T14:30:00+01:00'."
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone or resolve_zone(None))
    return parsed


def describe_datetime(moment: datetime, zone_name: str | None = None) -> dict[str, Any]:
    offset = moment.utcoffset()
    iso_year, iso_week, _ = moment.isocalendar()
    return {
        "iso": moment.isoformat(timespec="seconds"),
        "date": moment.date().isoformat(),
        "time": moment.strftime("%H:%M:%S"),
        "weekday": moment.strftime("%A"),
        "iso_week": f"{iso_year}-W{iso_week:02d}",
        "timezone": zone_name or moment.tzname(),
        "utc_offset": moment.strftime("%z"),
        "utc_offset_minutes": int(offset.total_seconds() // 60) if offset else 0,
        "unix": int(moment.timestamp()),
    }


def convert_timezone(
    time: str, to_timezone: str, from_timezone: str | None = None
) -> dict[str, Any]:
    source = parse_datetime(time, resolve_zone(from_timezone))
    return {
        "from": describe_datetime(source, from_timezone if source.tzinfo is not UTC else "UTC"),
        "to": describe_datetime(source.astimezone(resolve_zone(to_timezone)), to_timezone),
    }


def _add_months(day: datetime, months: int) -> datetime:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return day.replace(year=year, month=month, day=min(day.day, last))


def date_math(
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
    zone = resolve_zone(timezone)
    first = parse_datetime(start, zone)
    if end is not None:
        second = parse_datetime(end, zone)
        delta = second - first
        total = int(delta.total_seconds())
        sign = -1 if total < 0 else 1
        rest = abs(total)
        d, rest = divmod(rest, 86400)
        h, rest = divmod(rest, 3600)
        m, s = divmod(rest, 60)
        return {
            "start": first.isoformat(timespec="seconds"),
            "end": second.isoformat(timespec="seconds"),
            "total_seconds": total,
            "total_days": round(total / 86400, 4),
            "breakdown": f"{'-' if sign < 0 else ''}{d}d {h}h {m}m {s}s",
            "business_days": _business_days(first.date(), second.date()),
        }
    shifted = _add_months(first, years * 12 + months) + timedelta(
        weeks=weeks, days=days, hours=hours, minutes=minutes
    )
    return {"start": first.isoformat(timespec="seconds"), **describe_datetime(shifted)}


def _business_days(a: date, b: date) -> int:
    """Weekdays from ``a`` up to (not including) ``b``; negative when ``b`` is earlier."""
    if b < a:
        return -_business_days(b, a)
    count = 0
    cursor = a
    while cursor < b:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


# -- calculator ------------------------------------------------------------
_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": lambda *a: sum(a),
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "radians": math.radians,
    "degrees": math.degrees,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_EXPONENT = 10_000
_MAX_EXPR_LEN = 500


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ToolError("Exponent too large.")
        return _BIN_OPS[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 1000:
            raise ToolError("Factorial argument too large.")
        return _FUNCS[node.func.id](*args)
    raise ToolError(
        "Unsupported expression. Use numbers, + - * / // % **, parentheses, and math functions."
    )


def calculate(expression: str) -> dict[str, Any]:
    text = expression.strip().replace("^", "**")
    if not text or len(text) > _MAX_EXPR_LEN:
        raise ToolError(f"Expression must be 1-{_MAX_EXPR_LEN} characters.")
    try:
        tree = ast.parse(text, mode="eval")
        result = _eval(tree)
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("Division by zero.") from None
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ToolError(f"Could not evaluate: {exc}") from None
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    return {"expression": expression, "result": result}


# Factor to the base unit of each dimension; temperature is handled separately.
_UNITS: dict[str, dict[str, float]] = {
    "length": {
        "mm": 0.001,
        "cm": 0.01,
        "m": 1.0,
        "km": 1000.0,
        "in": 0.0254,
        "ft": 0.3048,
        "yd": 0.9144,
        "mi": 1609.344,
        "nmi": 1852.0,
    },
    "mass": {
        "mg": 1e-6,
        "g": 1e-3,
        "kg": 1.0,
        "t": 1000.0,
        "oz": 0.028349523125,
        "lb": 0.45359237,
    },
    "volume": {
        "ml": 1e-3,
        "l": 1.0,
        "m3": 1000.0,
        "tsp": 0.00492892159375,
        "tbsp": 0.01478676478125,
        "cup": 0.2365882365,
        "floz": 0.0295735295625,
        "pt": 0.473176473,
        "qt": 0.946352946,
        "gal": 3.785411784,
    },
    "time": {
        "ms": 0.001,
        "s": 1.0,
        "min": 60.0,
        "h": 3600.0,
        "d": 86400.0,
        "wk": 604800.0,
    },
    "data": {
        "b": 1.0,
        "kb": 1e3,
        "mb": 1e6,
        "gb": 1e9,
        "tb": 1e12,
        "kib": 1024.0,
        "mib": 1024.0**2,
        "gib": 1024.0**3,
        "tib": 1024.0**4,
    },
    "speed": {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 1852 / 3600},
    "temperature": {"c": 1.0, "f": 1.0, "k": 1.0},
}
_UNIT_ALIASES = {
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "kilometer": "km",
    "kilometers": "km",
    "mile": "mi",
    "miles": "mi",
    "inch": "in",
    "inches": "in",
    "foot": "ft",
    "feet": "ft",
    "yard": "yd",
    "yards": "yd",
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "ounce": "oz",
    "ounces": "oz",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "gallon": "gal",
    "gallons": "gal",
    "second": "s",
    "seconds": "s",
    "sec": "s",
    "minute": "min",
    "minutes": "min",
    "hour": "h",
    "hours": "h",
    "hr": "h",
    "day": "d",
    "days": "d",
    "week": "wk",
    "weeks": "wk",
    "celsius": "c",
    "°c": "c",
    "fahrenheit": "f",
    "°f": "f",
    "kelvin": "k",
    "byte": "b",
    "bytes": "b",
    "kph": "km/h",
    "knot": "kn",
    "knots": "kn",
}


def _unit(name: str) -> tuple[str, str]:
    key = name.strip().lower()
    key = _UNIT_ALIASES.get(key, key)
    for dimension, table in _UNITS.items():
        if key in table:
            return dimension, key
    raise ToolError(f"Unknown unit: {name!r}.")


def convert_units(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    dim_from, src = _unit(from_unit)
    dim_to, dst = _unit(to_unit)
    if dim_from != dim_to:
        raise ToolError(f"Cannot convert {dim_from} ({from_unit}) to {dim_to} ({to_unit}).")
    if dim_from == "temperature":
        celsius = {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[src]
        result = {"c": celsius, "f": celsius * 9 / 5 + 32, "k": celsius + 273.15}[dst]
    else:
        table = _UNITS[dim_from]
        result = value * table[src] / table[dst]
    return {
        "value": value,
        "from": src,
        "to": dst,
        "result": round(result, 10),
        "dimension": dim_from,
    }


# -- ids and randomness ----------------------------------------------------
def uuid7() -> uuid.UUID:
    """A time-ordered UUID (RFC 9562 v7); stdlib only has it from Python 3.14."""
    import time

    millis = time.time_ns() // 1_000_000
    rand = secrets.randbits(74)
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (rand >> 62) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def generate_uuid(version: int = 4, count: int = 1) -> dict[str, Any]:
    if version not in (4, 7):
        raise ToolError("version must be 4 (random) or 7 (time-ordered).")
    count = _clamp_count(count)
    make = uuid.uuid4 if version == 4 else uuid7
    return {"version": version, "uuids": [str(make()) for _ in range(count)]}


def _clamp_count(count: int) -> int:
    if not 1 <= count <= 100:
        raise ToolError("count must be between 1 and 100.")
    return count


def random_value(
    kind: str = "token_urlsafe",
    length: int = 32,
    minimum: int = 0,
    maximum: int = 100,
    count: int = 1,
) -> dict[str, Any]:
    count = _clamp_count(count)
    if kind in ("token_hex", "token_urlsafe"):
        if not 1 <= length <= 1024:
            raise ToolError("length (bytes) must be between 1 and 1024.")
        make = secrets.token_hex if kind == "token_hex" else secrets.token_urlsafe
        return {"kind": kind, "values": [make(length) for _ in range(count)]}
    if kind == "integer":
        if minimum > maximum:
            raise ToolError("minimum must not exceed maximum.")
        return {
            "kind": kind,
            "minimum": minimum,
            "maximum": maximum,
            "values": [minimum + secrets.randbelow(maximum - minimum + 1) for _ in range(count)],
        }
    raise ToolError("kind must be one of: token_urlsafe, token_hex, integer.")


# -- hashing and encoding --------------------------------------------------
_HASHES = ("md5", "sha1", "sha256", "sha512")
HASH_ENCODE_OPERATIONS = (
    *_HASHES,
    "base64_encode",
    "base64_decode",
    "url_encode",
    "url_decode",
    "hex_encode",
    "hex_decode",
    "html_escape",
    "html_unescape",
)
_MAX_TEXT = 1_000_000


def hash_or_encode(operation: str, text: str) -> dict[str, Any]:
    if len(text) > _MAX_TEXT:
        raise ToolError(f"text is limited to {_MAX_TEXT:,} characters.")
    op = operation.strip().lower()
    data = text.encode("utf-8")
    if op in _HASHES:
        result = hashlib.new(op, data).hexdigest()
    elif op == "base64_encode":
        result = base64.b64encode(data).decode("ascii")
    elif op == "base64_decode":
        try:
            padded = text.strip() + "=" * (-len(text.strip()) % 4)
            result = base64.b64decode(padded, validate=False).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            raise ToolError("Not valid base64-encoded UTF-8 text.") from None
    elif op == "url_encode":
        result = quote(text, safe="")
    elif op == "url_decode":
        result = unquote(text)
    elif op == "hex_encode":
        result = data.hex()
    elif op == "hex_decode":
        try:
            result = bytes.fromhex(text.strip()).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise ToolError("Not valid hex-encoded UTF-8 text.") from None
    elif op == "html_escape":
        result = html.escape(text)
    elif op == "html_unescape":
        result = html.unescape(text)
    else:
        raise ToolError(
            f"Unknown operation {operation!r}. Choose from: {', '.join(HASH_ENCODE_OPERATIONS)}."
        )
    return {"operation": op, "result": result}


# -- text ------------------------------------------------------------------
_WORD = re.compile(r"\w+(?:['\u2019-]\w+)*", re.UNICODE)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")


def count_text(text: str) -> dict[str, Any]:
    words = _WORD.findall(text)
    return {
        "characters": len(text),
        "characters_no_spaces": len(re.sub(r"\s", "", text)),
        "words": len(words),
        "lines": len(text.splitlines()) if text else 0,
        "sentences": len([s for s in _SENTENCE.findall(text) if re.search(r"\w", s)]),
        "paragraphs": len([p for p in re.split(r"\n\s*\n", text) if p.strip()]),
        "bytes_utf8": len(text.encode("utf-8")),
        # ~4 characters per token is the usual rule of thumb for English prose.
        "estimated_tokens": math.ceil(len(text) / 4),
        "reading_minutes": round(len(words) / 230, 2),
    }


# -- system ----------------------------------------------------------------
def system_info(app_version: str) -> dict[str, Any]:
    import locale
    import os

    now = datetime.now().astimezone()
    lang = locale.getlocale()[0] or os.environ.get("LANG", "")
    return {
        "app_version": app_version,
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "locale": lang,
        "timezone": now.tzname(),
        "utc_offset": now.strftime("%z"),
    }
