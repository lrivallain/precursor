"""Rainbow ASCII startup banner.

Renders the ``PRECURSOR`` wordmark as block-letter ASCII art with a horizontal
rainbow gradient (each column gets its own hue), mirroring the retro pixel-font
splash used elsewhere in the project. Colour is emitted only when the target
stream is a TTY and ``NO_COLOR`` is unset, so piped/redirected output stays
grep-clean — the same rule the logging config follows.
"""

from __future__ import annotations

import colorsys
import os
import sys
from typing import IO

_RESET = "\033[0m"
_BLOCK = "█"

# 5-row block glyphs for the letters in "PRECURSOR". Kept intentionally small so
# the banner fits an 80-column terminal. Each glyph is a fixed 5 columns wide;
# rows are padded to that width by the renderer.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "P": (
        "████ ",
        "█   █",
        "████ ",
        "█    ",
        "█    ",
    ),
    "R": (
        "████ ",
        "█   █",
        "████ ",
        "█  █ ",
        "█   █",
    ),
    "E": (
        "█████",
        "█    ",
        "████ ",
        "█    ",
        "█████",
    ),
    "C": (
        " ████",
        "█    ",
        "█    ",
        "█    ",
        " ████",
    ),
    "U": (
        "█   █",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ),
    "S": (
        " ████",
        "█    ",
        " ███ ",
        "    █",
        "████ ",
    ),
    "O": (
        " ███ ",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ),
}

_ROWS = 5
_GLYPH_GAP = " "  # blank column between letters


def use_color(stream: IO[str] | None = None) -> bool:
    """Whether to emit ANSI colour for ``stream`` (defaults to stderr)."""
    stream = stream or sys.stderr
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _column_color(fraction: float) -> str:
    """A truecolor ANSI escape for a rainbow hue at ``fraction`` (0..1) width."""
    # Sweep red → violet without wrapping back to red at the far edge.
    hue = 0.82 * max(0.0, min(1.0, fraction))
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return f"\033[38;2;{int(r * 255)};{int(g * 255)};{int(b * 255)}m"


def _render_rows(text: str) -> list[str]:
    """The plain (uncoloured) block-letter rows for ``text``."""
    segments: list[list[str]] = [[] for _ in range(_ROWS)]
    for char in text.upper():
        glyph = _GLYPHS.get(char)
        if glyph is None:
            continue
        for row in range(_ROWS):
            segments[row].append(glyph[row])
    return [_GLYPH_GAP.join(row) for row in segments]


def _colorize(row: str, width: int) -> str:
    out: list[str] = []
    for col, pixel in enumerate(row):
        if pixel == " ":
            out.append(" ")
            continue
        out.append(_column_color(col / max(width - 1, 1)) + pixel)
    out.append(_RESET)
    return "".join(out)


def render(text: str = "PRECURSOR", *, color: bool = True, indent: str = "  ") -> list[str]:
    """Return the banner as a list of lines (no trailing newlines).

    When ``color`` is false the raw block art is returned so redirected output
    stays readable and free of escape codes.
    """
    rows = _render_rows(text)
    width = max((len(row) for row in rows), default=0)
    if not color:
        return [indent + row for row in rows]
    return [indent + _colorize(row, width) for row in rows]
