"""The nightly manifest is a contract between a workflow and a parser.

``.github/workflows/nightly.yml`` writes ``version.json``; ``services/updates.py``
reads it. Nothing else connects them, so renaming a key on one side would break
every client's update check with a green CI run and no failing test — the
workflow only runs on ``main``, after merge.

These tests run the workflow's *actual* manifest step and feed the result to the
*actual* parser, so the two cannot drift apart unnoticed.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import httpx
import pytest

from precursor.backend.services import updates

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "nightly.yml"


def _manifest_step() -> str:
    """The shell body of the workflow's `manifest` step."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["nightly"]["steps"]
    step = next(s for s in steps if s.get("id") == "manifest")
    return str(step["run"])


def _run_manifest_step(
    tmp_path: pathlib.Path, wheel_names: list[str]
) -> subprocess.CompletedProcess[str]:
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in wheel_names:
        (dist / name).write_bytes(b"not a real wheel")
    script = tmp_path / "step.sh"
    script.write_text(_manifest_step(), encoding="utf-8")
    return subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{pathlib.Path(sys.executable).parent}:/usr/bin:/bin",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        },
    )


HOST = "precursor_ai-2026.7.1.dev229+gabcdef123-py3-none-any.whl"
# Built-in plugins inherit the host's CalVer, so a plugin wheel carries the same
# commit-bearing version. It used to be a static `0.1.0`, which made the
# manifest advertise an identical URL on every build and left clients unable to
# tell one nightly's plugin from the next.
PLUGIN = "precursor_kanban-2026.7.1.dev229+gabcdef123-py3-none-any.whl"


@pytest.mark.skipif(sys.platform == "win32", reason="the step is bash")
def test_the_workflow_manifest_is_read_back_by_the_update_check(tmp_path: pathlib.Path) -> None:
    """The whole point: producer and consumer agree on every field."""
    result = _run_manifest_step(tmp_path, [HOST, PLUGIN])
    assert result.returncode == 0, result.stderr

    payload = json.loads((tmp_path / "dist" / "version.json").read_text(encoding="utf-8"))
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))
    with httpx.Client(transport=transport) as client:
        version, commit, wheel_url, extras = updates._check_nightly(client, "owner/repo")

    # Every one of these being non-None means the parser found the key the
    # workflow wrote. A rename on either side fails here.
    assert version == "2026.7.1.dev229+gabcdef123"
    assert commit == "012345678"  # first 9 of GITHUB_SHA
    assert wheel_url is not None and wheel_url.endswith(HOST)
    assert extras == (f"https://github.com/owner/repo/releases/download/nightly/{PLUGIN}",)


@pytest.mark.skipif(sys.platform == "win32", reason="the step is bash")
def test_the_manifest_is_valid_json_and_exports_the_version(tmp_path: pathlib.Path) -> None:
    result = _run_manifest_step(tmp_path, [HOST, PLUGIN])
    assert result.returncode == 0, result.stderr

    payload = json.loads((tmp_path / "dist" / "version.json").read_text(encoding="utf-8"))
    assert payload["channel"] == "nightly"
    assert payload["full_commit"] == "0123456789abcdef0123456789abcdef01234567"
    # The release step titles the release with this.
    assert "version=2026.7.1.dev229+gabcdef123" in (tmp_path / "gh_output").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="the step is bash")
def test_a_build_with_no_plugins_still_produces_a_valid_manifest(tmp_path: pathlib.Path) -> None:
    result = _run_manifest_step(tmp_path, [HOST])
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "dist" / "version.json").read_text(encoding="utf-8"))
    assert payload["extra_wheel_urls"] == []


@pytest.mark.skipif(sys.platform == "win32", reason="the step is bash")
def test_two_host_wheels_fail_the_step(tmp_path: pathlib.Path) -> None:
    """Publishing a manifest that points at the wrong build is worse than not
    publishing: clients would install it and never notice."""
    result = _run_manifest_step(tmp_path, [HOST, "precursor_ai-9999.1.0-py3-none-any.whl", PLUGIN])
    assert result.returncode != 0
    assert "Expected exactly 1 host wheel" in result.stderr
