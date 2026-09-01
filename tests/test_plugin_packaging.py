"""Packaging invariants for the built-in plugins.

A plugin in this repository is a separate distribution but not an independent
one: it is built, released and installed alongside the host. These guard the two
properties that make that work, both of which fail *silently* — the symptom is
an install that looks upgraded and isn't.
"""

from __future__ import annotations

import pathlib
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"


def _plugin_manifests() -> list[pathlib.Path]:
    return sorted(PLUGINS_DIR.glob("*/pyproject.toml"))


def test_there_is_at_least_one_built_in_plugin() -> None:
    """Guards the parametrised tests below against silently covering nothing."""
    assert _plugin_manifests(), "no plugin manifests found under plugins/"


@pytest.mark.parametrize("manifest", _plugin_manifests(), ids=lambda p: p.parent.name)
def test_a_built_in_plugin_inherits_the_host_version(manifest: pathlib.Path) -> None:
    """No static ``version`` — it must come from the host's git tags.

    A pinned version produces the same wheel filename on every build. The
    nightly manifest then advertises an unchanging URL, uv finds the requirement
    already satisfied and never downloads it, so the host advances while the
    plugin stays frozen at whatever build landed first. Publishing breaks for
    the same reason: PyPI refuses to re-upload a version.
    """
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    project = data["project"]

    assert "version" not in project, (
        f"{manifest.parent.name} pins a static version. Built-in plugins take "
        "the host's CalVer: use dynamic = ['version'] with hatch-vcs."
    )
    assert "version" in project.get("dynamic", []), (
        f"{manifest.parent.name} must declare dynamic = ['version']"
    )
    assert data["tool"]["hatch"]["version"]["source"] == "vcs"
    assert "hatch-vcs" in data["build-system"]["requires"]


@pytest.mark.parametrize("manifest", _plugin_manifests(), ids=lambda p: p.parent.name)
def test_the_version_root_points_at_the_host_repository(manifest: pathlib.Path) -> None:
    """``root`` is what makes a workspace member read the host's tags.

    It is also the single line to drop if the plugin is ever extracted to its
    own repository, so it is worth stating explicitly rather than leaving it to
    hatch-vcs's default of "the directory I am in".
    """
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    raw_options = data["tool"]["hatch"]["version"]["raw-options"]
    root = (manifest.parent / raw_options["root"]).resolve()
    assert root == REPO_ROOT, f"expected {REPO_ROOT}, got {root}"
