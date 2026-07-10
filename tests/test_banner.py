"""Startup banner rendering — plain vs coloured, and TTY/NO_COLOR gating."""

from __future__ import annotations

import io

from precursor.backend import banner


def test_render_plain_has_no_ansi_and_reads_precursor() -> None:
    lines = banner.render(color=False)
    assert len(lines) == 5
    assert all("\033[" not in line for line in lines)
    # Every glyph in the word is present at least once in the block art.
    joined = "\n".join(lines)
    assert banner._BLOCK in joined
    # No trailing newlines in the returned lines.
    assert all(not line.endswith("\n") for line in lines)


def test_render_color_emits_truecolor_escapes_and_resets() -> None:
    lines = banner.render(color=True)
    art = "\n".join(lines)
    assert "\033[38;2;" in art  # truecolor foreground
    assert all(line.endswith(banner._RESET) for line in lines)


def test_use_color_respects_no_color(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tty = io.StringIO()
    monkeypatch.setattr(tty, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert banner.use_color(tty) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert banner.use_color(tty) is False


def test_use_color_false_for_non_tty() -> None:
    assert banner.use_color(io.StringIO()) is False
