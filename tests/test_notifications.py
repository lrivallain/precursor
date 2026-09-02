"""Actionable desktop notifications.

Noticing a new build in the background is only half the feature: without a
button, the user still has to go and find the tray menu. These tests pin the
half that can go wrong silently — parsing the platform helper's answer, and
never mistaking "no answer" for consent to restart the app.

Nothing here spawns a real dialog; every platform helper is stubbed.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from precursor.backend import notifications

_APPLY = notifications.Choice("apply", "Update and restart")
_LATER = notifications.Choice("later", "Later")
_CHOICES = (_APPLY, _LATER)


def _osascript(monkeypatch: pytest.MonkeyPatch, stdout: str, *, returncode: int = 0) -> list[str]:
    """Pretend to be macOS with an ``osascript`` that answers ``stdout``."""
    script: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        script.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(notifications.sys, "platform", "darwin")
    monkeypatch.setattr(notifications.shutil, "which", lambda _n: "/usr/bin/osascript")
    monkeypatch.setattr(notifications.subprocess, "run", fake_run)
    return script


def test_the_chosen_button_comes_back_as_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _osascript(monkeypatch, "button returned:Update and restart, gave up:false\n")
    assert notifications.ask("t", "m", _CHOICES, default=_APPLY.key) == "apply"


def test_the_other_button_is_not_the_default_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _osascript(monkeypatch, "button returned:Later, gave up:false\n")
    assert notifications.ask("t", "m", _CHOICES, default=_APPLY.key) == "later"


def test_a_dialog_that_gave_up_chose_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """AppleScript still names the *default* button when it times out, so the
    give-up flag has to veto it — otherwise a prompt nobody saw would install an
    update and restart the app."""
    _osascript(monkeypatch, "button returned:Update and restart, gave up:true\n")
    assert notifications.ask("t", "m", _CHOICES, default=_APPLY.key) is None


def test_escaping_the_dialog_chooses_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pressing Escape is `osascript` exit 1 (-128 User canceled)."""
    _osascript(monkeypatch, "", returncode=1)
    assert notifications.ask("t", "m", _CHOICES) is None


def test_an_unrecognised_answer_chooses_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _osascript(monkeypatch, "button returned:Something else, gave up:false\n")
    assert notifications.ask("t", "m", _CHOICES) is None


def test_the_default_button_is_named_in_the_script(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _osascript(monkeypatch, "button returned:Later, gave up:false\n")
    notifications.ask("Title", "Message", _CHOICES, default=_APPLY.key, timeout=42)
    assert 'default button "Update and restart"' in script[0]
    assert "giving up after 42" in script[0]
    assert '"Update and restart", "Later"' in script[0]


def test_quotes_in_the_message_do_not_break_the_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version string is tame, but the message is built from remote data."""
    script = _osascript(monkeypatch, "button returned:Later, gave up:false\n")
    notifications.ask('He said "hi"\\', "m", _CHOICES)
    assert '\\"hi\\"' in script[0]
    assert "\\\\" in script[0]


def test_a_broken_helper_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifications.sys, "platform", "darwin")
    monkeypatch.setattr(notifications.shutil, "which", lambda _n: "/usr/bin/osascript")

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("no window server")

    monkeypatch.setattr(notifications.subprocess, "run", boom)
    assert notifications.ask("t", "m", _CHOICES) is None


def test_no_choices_is_not_a_prompt() -> None:
    assert notifications.ask("t", "m", ()) is None


def test_windows_admits_it_cannot_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers branch on this to fall back to a plain toast rather than
    swallowing the announcement entirely."""
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    monkeypatch.setattr(notifications.os, "name", "nt")
    assert notifications.can_ask() is False
    assert notifications.ask("t", "m", _CHOICES) is None


# --- freedesktop --------------------------------------------------------------


def _notify_send(
    monkeypatch: pytest.MonkeyPatch, stdout: str, *, returncode: int = 0
) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(notifications.sys, "platform", "linux")
    monkeypatch.setattr(notifications.os, "name", "posix")
    monkeypatch.setattr(notifications, "_notify_send", lambda: "/usr/bin/notify-send")
    monkeypatch.setattr(notifications.subprocess, "run", fake_run)
    return seen


def test_notify_send_actions_are_passed_and_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _notify_send(monkeypatch, "apply\n")
    assert notifications.ask("Title", "Message", _CHOICES) == "apply"
    assert "--action=apply=Update and restart" in seen[0]
    assert "--wait" in seen[0]


def test_a_dismissed_notification_chooses_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _notify_send(monkeypatch, "")
    assert notifications.ask("t", "m", _CHOICES) is None


def test_an_old_notify_send_is_not_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Actions landed in libnotify 0.8; older builds reject `--action`, so the
    help text is what decides."""
    monkeypatch.setattr(notifications.sys, "platform", "linux")
    monkeypatch.setattr(notifications.os, "name", "posix")
    monkeypatch.setattr(notifications.shutil, "which", lambda _n: "/usr/bin/notify-send")
    monkeypatch.setattr(
        notifications.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout="  -u, --urgency\n"),
    )
    notifications._notify_send.cache_clear()
    try:
        assert notifications._notify_send() is None
        assert notifications.can_ask() is False
    finally:
        notifications._notify_send.cache_clear()
