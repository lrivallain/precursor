"""Update discovery — channel selection and "is this newer than me" logic.

The two channels answer that question differently on purpose: tagged releases
are ordered so they compare by version, while dev builds are not (two branches
can share a base version), so they compare by commit.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from precursor.backend import config
from precursor.backend.services import updates


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    updates.invalidate()
    config.get_settings.cache_clear()
    yield
    updates.invalidate()
    config.get_settings.cache_clear()


def test_dev_version_defaults_to_nightly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.7.1.dev227+gec9a0145f.d20260826")
    assert updates.default_channel() == "nightly"


def test_tagged_version_defaults_to_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.7.0")
    assert updates.default_channel() == "stable"


def test_configured_channel_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_UPDATE_CHANNEL", "stable")
    config.get_settings.cache_clear()
    monkeypatch.setattr(updates, "__version__", "2026.7.1.dev1+gabc.d20260826")
    assert updates.default_channel() == "stable"


def test_version_ordering_is_calver_numeric() -> None:
    key = updates._version_key
    assert key("2026.7.0") < key("2026.7.1")
    assert key("2026.7.0") < key("2026.10.0")  # not lexicographic
    assert key("2026.7.0") < key("2027.1.0")
    # A dev build compares by its base version, ignoring the local part.
    assert key("2026.7.1.dev5+gabc.d20260826") == key("2026.7.1")


def _client_returning(payload: Any, *, url_check: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if url_check:
            assert url_check in str(request.url)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    original = httpx.Client

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(updates.httpx, "Client", factory)


def test_nightly_reports_an_update_on_a_different_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.7.1.dev227+gaaaaaaaaa.d20260826")
    _patch_client(
        monkeypatch,
        _client_returning(
            {
                "version": "2026.7.1.dev230",
                "commit": "bbbbbbbbb",
                "wheel_url": "https://example.invalid/precursor_ai-2026.7.1.dev230-py3-none-any.whl",
                "extra_wheel_urls": [
                    "https://example.invalid/precursor_kanban-0.1-py3-none-any.whl"
                ],
            },
            url_check="releases/download/nightly/version.json",
        ),
    )
    info = updates.check(force=True)
    assert info.channel == "nightly"
    assert info.update_available is True
    assert info.latest_commit == "bbbbbbbbb"
    assert info.extra_wheel_urls == (
        "https://example.invalid/precursor_kanban-0.1-py3-none-any.whl",
    )


def test_nightly_reports_no_update_on_the_same_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.7.1.dev227+gaaaaaaaaa.d20260826")
    _patch_client(
        monkeypatch,
        _client_returning({"version": "2026.7.1.dev227", "commit": "aaaaaaaaa", "wheel_url": "x"}),
    )
    assert updates.check(force=True).update_available is False


def test_stable_compares_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.7.0")
    _patch_client(
        monkeypatch,
        _client_returning(
            {
                "tag_name": "v2026.8.0",
                "assets": [
                    {
                        "name": "precursor_ai-2026.8.0-py3-none-any.whl",
                        "browser_download_url": "https://example.invalid/w.whl",
                    }
                ],
            }
        ),
    )
    info = updates.check(force=True)
    assert info.channel == "stable"
    assert info.update_available is True
    assert info.latest_version == "2026.8.0"


def test_a_failed_check_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    info = updates.check(force=True)
    assert info.error is not None
    # "couldn't ask" must never be presented as "up to date".
    assert info.update_available is False


def test_results_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"version": "1", "commit": "c", "wheel_url": "w"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    updates.check(force=True)
    updates.check()
    updates.check()
    assert calls["n"] == 1


def test_source_checkout_is_the_install_mode_here() -> None:
    assert updates.install_mode() == "source"


def _write_receipt(
    tmp_path: Path,
    extras: list[str],
    *,
    siblings: tuple[str, ...] = ("precursor-kanban",),
    url: str = "",
) -> None:
    host = f'{{ name = "precursor-ai", extras = {json.dumps(extras)}'
    host += f', url = "{url}" }}' if url else " }"
    entries = [host, *(f'{{ name = "{name}" }}' for name in siblings)]
    (tmp_path / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = [\n" + "".join(f"    {e},\n" for e in entries) + "]\n",
        encoding="utf-8",
    )


def test_extras_are_read_from_the_install_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """What is installed is the truth; a configured list is a copy that drifts."""
    _write_receipt(tmp_path, ["tray", "agents"])
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path))
    assert updates.installed_extras() == ("tray", "agents")


def test_an_update_does_not_silently_drop_installed_extras(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug this guards: installing `[tray,agents]` and then updating against
    the default `kanban` uninstalled the menu-bar icon and Agents mode without
    saying anything."""
    _write_receipt(tmp_path, ["tray", "agents"])
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path))
    monkeypatch.setenv("PRECURSOR_UPDATE_EXTRAS", "kanban")
    config.get_settings.cache_clear()

    requirement = updates._requirement()
    for extra in ("tray", "agents", "kanban"):
        assert extra in requirement, requirement


def test_the_setting_can_still_add_an_extra_not_yet_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_receipt(tmp_path, ["tray"])
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path))
    monkeypatch.setenv("PRECURSOR_UPDATE_EXTRAS", "agents")
    config.get_settings.cache_clear()
    assert updates._requirement() == "precursor-ai[tray,agents]"


def test_a_missing_receipt_falls_back_to_the_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path / "nothing-here"))
    monkeypatch.setenv("PRECURSOR_UPDATE_EXTRAS", "kanban")
    config.get_settings.cache_clear()
    assert updates.installed_extras() == ()
    assert updates._requirement() == "precursor-ai[kanban]"


def test_a_corrupt_receipt_is_not_fatal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "uv-receipt.toml").write_text("this is not toml [[[", encoding="utf-8")
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path))
    assert updates.installed_extras() == ()


def test_the_setting_can_drop_an_extra_the_receipt_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The escape hatch: an index that will never carry a plugin must be
    survivable without reinstalling the tool by hand."""
    _write_receipt(tmp_path, ["tray", "kanban"])
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path))
    monkeypatch.setenv("PRECURSOR_UPDATE_EXTRAS", "-kanban")
    config.get_settings.cache_clear()
    assert updates._requirement() == "precursor-ai[tray]"


def test_an_extra_pulling_only_a_precursor_plugin_is_optional() -> None:
    """`kanban` pulls `precursor-kanban` (a separate distribution); `tray` pulls
    third-party libraries the host itself uses."""
    assert updates.plugin_extras(["kanban", "tray", "postgres"]) == ("kanban",)


def _uv_resolution_failure() -> updates.UpdateError:
    return updates.UpdateError(
        "No solution found when resolving dependencies: Because precursor-kanban "
        "was not found in the package registry […]"
    )


def _record_installs(monkeypatch: pytest.MonkeyPatch, *, fails: str | None) -> list[list[str]]:
    """Capture the install commands, failing the ones that ask for ``fails``."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path | None = None) -> str:
        calls.append(cmd)
        if fails is not None and any(fails in arg for arg in cmd):
            raise _uv_resolution_failure()
        return ""

    monkeypatch.setattr(updates, "_run", fake_run)
    monkeypatch.setattr(updates.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        updates,
        "_extra_requirements",
        lambda: {"kanban": ("precursor-kanban",), "tray": ("pystray", "pillow")},
    )
    return calls


def _uv_tool_info() -> updates.UpdateInfo:
    return updates.UpdateInfo(
        current_version="2026.7.1",
        current_commit=None,
        latest_version="2026.8.0",
        latest_commit=None,
        update_available=True,
        channel="stable",
        install_mode="uv-tool",
    )


def _install_extras(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extras: list[str],
    *,
    siblings: tuple[str, ...] = ("precursor-kanban",),
) -> None:
    _write_receipt(tmp_path, extras, siblings=siblings)
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path))
    monkeypatch.setenv("PRECURSOR_UPDATE_EXTRAS", "")
    config.get_settings.cache_clear()


def test_an_unresolvable_plugin_does_not_block_the_host_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug this guards: an index missing one optional plugin stranded the
    whole install on its old build."""
    _install_extras(monkeypatch, tmp_path, ["tray", "kanban"])
    calls = _record_installs(monkeypatch, fails="kanban")

    summary = updates.apply(_uv_tool_info())

    assert len(calls) == 2
    # The retry keeps everything the host itself needs …
    assert "precursor-ai[tray]" in calls[1]
    # … and the result says what was given up, rather than claiming success.
    assert "2026.8.0" in summary
    assert "kanban" in summary
    assert "precursor-kanban was not found" in summary


def test_a_failure_unrelated_to_the_plugin_still_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dropping the plugin must not turn a real breakage into a fake success."""
    _install_extras(monkeypatch, tmp_path, ["tray", "kanban"])
    calls = _record_installs(monkeypatch, fails="precursor-ai")

    with pytest.raises(updates.UpdateError, match="precursor-kanban was not found"):
        updates.apply(_uv_tool_info())
    assert len(calls) == 2  # tried, then gave up


def test_nothing_is_retried_when_nothing_is_droppable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_extras(monkeypatch, tmp_path, ["tray"], siblings=())
    calls = _record_installs(monkeypatch, fails="precursor-ai")

    with pytest.raises(updates.UpdateError):
        updates.apply(_uv_tool_info())
    assert len(calls) == 1


def test_a_failed_command_leads_with_the_reason_not_the_command() -> None:
    """A tray notification truncates, so the command must not come first — that
    is what left "updating failed" as the only visible signal."""
    script = (
        "import sys; sys.stderr.write("
        "'\\u00d7 No solution found:\\n\\u2570\\u2500\\u25b6 Because precursor-kanban\\n"
        "    was not found in the registry.\\n'); sys.exit(1)"
    )
    url = "https://github.com/o/r/releases/download/nightly/precursor_ai-1-py3-none-any.whl"

    with pytest.raises(updates.UpdateError) as caught:
        updates._run([sys.executable, "-c", script, f"precursor-ai[kanban] @ {url}"])

    message = str(caught.value)
    assert message.startswith("No solution found: Because precursor-kanban was not found")
    # The command is still there, but with the URL collapsed to its filename.
    assert "…/precursor_ai-1-py3-none-any.whl" in message
    assert "exited 1" in message


# --- out-of-tree plugins ----------------------------------------------------
#
# A plugin that ships from its own repository has no extra in core's metadata to
# be named by, so uv's receipt is the only record that it was ever asked for.
# `uv tool install` rebuilds the environment from its arguments, which makes
# re-stating those siblings the whole of "the install persists".


def test_sibling_distributions_are_read_from_the_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_receipt(tmp_path, ["tray"], siblings=("precursor-kanban", "precursor-notes"))
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path))
    assert updates.installed_plugins() == ("precursor-kanban", "precursor-notes")


def test_an_update_reinstalls_out_of_tree_plugins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug this guards: `precursor service update` rebuilt the environment
    from the extras alone, so every plugin installed with `--with` — which is
    every plugin that lives outside this repository — was silently uninstalled
    on each update."""
    _install_extras(monkeypatch, tmp_path, ["tray"], siblings=("precursor-notes",))
    calls = _record_installs(monkeypatch, fails=None)

    updates.apply(_uv_tool_info())

    assert len(calls) == 1
    assert calls[0].count("--with") == 1
    assert "precursor-notes" in calls[0]


def test_a_same_commit_wheel_supersedes_the_plain_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both sources naming one distribution must install it once, from the
    pinned wheel — a nightly host paired with a PyPI plugin is the mismatch
    publishing them together exists to avoid."""
    _install_extras(monkeypatch, tmp_path, ["tray"], siblings=("precursor-kanban",))
    calls = _record_installs(monkeypatch, fails=None)
    wheel = "https://example.invalid/nightly/precursor_kanban-2026.9-py3-none-any.whl"

    updates.apply(replace(_uv_tool_info(), extra_wheel_urls=(wheel,)))

    assert calls[0].count("--with") == 1
    assert wheel in calls[0]
    assert "precursor-kanban" not in calls[0]


def test_a_plugin_the_index_cannot_serve_does_not_strand_the_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sibling is optional by construction, exactly like a plugin extra: an
    updated host missing one board beats no update at all."""
    _install_extras(monkeypatch, tmp_path, ["tray"], siblings=("precursor-notes",))
    calls = _record_installs(monkeypatch, fails="precursor-notes")

    summary = updates.apply(_uv_tool_info())

    assert len(calls) == 2
    assert "--with" not in calls[1]
    assert "precursor-ai[tray]" in calls[1]
    assert "precursor-notes" in summary
