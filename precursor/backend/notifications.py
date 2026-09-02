"""Desktop notifications the user can *act* on.

``pystray.Icon.notify`` shows a toast and nothing more, which is fine for
"updated" but wrong for "an update is waiting": the whole value of noticing a
new build in the background is being able to take it without hunting for the
menu. So this module adds the one thing the toast can't do — buttons — using
whatever the platform already ships, and says so honestly when it can't.

Deliberately dependency-free and best-effort. :func:`can_ask` is the predicate
callers branch on, so a platform with no actionable path degrades to a plain
toast rather than silently swallowing the prompt.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)

# Long enough to notice and decide, short enough that a prompt raised while you
# were away is gone by the time you come back.
DEFAULT_TIMEOUT = 90.0


@dataclass(frozen=True)
class Choice:
    """One button. ``key`` is what the caller branches on, ``label`` what it says."""

    key: str
    label: str


def _osascript_string(value: str) -> str:
    """Escape a Python string into an AppleScript string literal.

    Order matters: backslashes first, then quotes, and real newlines last —
    they become the two-character ``\\n`` escape, which must not itself be
    re-escaped. AppleScript literals cannot span source lines, so a raw newline
    would be a syntax error rather than a wrapped message.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@lru_cache(maxsize=1)
def _notify_send() -> str | None:
    """``notify-send`` when it is new enough to carry action buttons.

    Actions landed in libnotify 0.8; older builds accept ``--action`` as an
    unknown option and fail, so the help text is the cheap way to ask.
    """
    binary = shutil.which("notify-send")
    if binary is None:
        return None
    try:
        help_text = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, check=False, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        return None
    return binary if "--action" in help_text else None


def can_ask() -> bool:
    """Whether :func:`ask` can actually put buttons in front of the user."""
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    if os.name == "nt":  # pragma: no cover - Windows-only path
        # A toast with buttons needs the WinRT stack; nothing dependency-free
        # gets there, and a MessageBox is not a notification.
        return False
    return _notify_send() is not None


def ask(
    title: str,
    message: str,
    choices: Sequence[Choice],
    *,
    default: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Prompt with buttons and return the chosen ``key``.

    ``None`` means "no decision": dismissed, timed out, unsupported, or the
    helper failed. Callers must treat it as *do nothing* — never as consent.
    """
    if not choices:
        return None
    try:
        if sys.platform == "darwin":
            return _ask_macos(title, message, choices, default=default, timeout=timeout)
        if os.name != "nt":
            return _ask_freedesktop(title, message, choices, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Actionable notification failed: %s", exc)
    return None


def _ask_macos(
    title: str,
    message: str,
    choices: Sequence[Choice],
    *,
    default: str | None,
    timeout: float,
) -> str | None:
    # `display dialog` tops out at three buttons, and the last one is the one
    # AppleScript treats as the default position.
    buttons = list(choices)[:3]
    labels = ", ".join(f'"{_osascript_string(c.label)}"' for c in buttons)
    chosen = next((c for c in buttons if c.key == default), buttons[-1])
    script = (
        f'display dialog "{_osascript_string(message)}" '
        f'with title "{_osascript_string(title)}" '
        f"buttons {{{labels}}} "
        f'default button "{_osascript_string(chosen.label)}" '
        f"with icon note giving up after {int(timeout)}"
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        # The dialog dismisses itself, but a wedged osascript must not pin a
        # daemon thread forever.
        timeout=timeout + 30,
    )
    if result.returncode != 0:
        # Exit 1 is the ordinary "user pressed Escape" (-128), not a fault.
        logger.debug("osascript prompt returned %s: %s", result.returncode, result.stderr.strip())
        return None
    return _parse_osascript(result.stdout, buttons)


def _parse_osascript(stdout: str, choices: Sequence[Choice]) -> str | None:
    """Read AppleScript's ``button returned:X, gave up:false`` record.

    A dialog that gave up still names a button — the default one — so the
    give-up flag has to veto it, or a prompt nobody saw would install an update.
    """
    fields = {}
    for part in stdout.strip().split(", "):
        key, _, value = part.partition(":")
        fields[key.strip()] = value.strip()
    if fields.get("gave up") == "true":
        return None
    label = fields.get("button returned")
    return next((c.key for c in choices if c.label == label), None)


def _ask_freedesktop(
    title: str, message: str, choices: Sequence[Choice], *, timeout: float
) -> str | None:
    binary = _notify_send()
    if binary is None:
        return None
    cmd = [binary, "--wait", "--app-name=Precursor"]
    for choice in choices:
        cmd.append(f"--action={choice.key}={choice.label}")
    cmd += [title, message]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout + 30)
    if result.returncode != 0:
        logger.debug("notify-send prompt failed: %s", result.stderr.strip())
        return None
    # Prints the activated action's key, or nothing when it was just dismissed.
    key = result.stdout.strip()
    return key if any(c.key == key for c in choices) else None
