"""Release planning and retry safety, without writing to GitHub or PyPI."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)

TODAY = dt.date(2026, 9, 20)
OLD = "1" * 40
HEAD = "2" * 40
TAG = "v2026.9.0"


def published(tag=TAG, *, draft=False, prerelease=False, assets=()):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [
            {"name": name, "state": "uploaded", "size": 100, "id": index}
            for index, name in enumerate(assets)
        ],
    }


def ci_run(**overrides):
    return {
        "id": 10,
        "head_sha": HEAD,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        **overrides,
    }


def test_first_release_ignores_the_rolling_tag():
    plan = release.select_release(HEAD, {"nightly": HEAD}, [], TODAY)
    assert (plan.tag, plan.sha, plan.previous_tag) == (TAG, HEAD, "")


def test_unchanged_main_does_not_cut_another_release():
    plan = release.select_release(HEAD, {TAG: HEAD, "nightly": HEAD}, [published()], TODAY)
    assert not plan.publish


def test_numeric_micro_counter_not_lexical_sort():
    tags = {"v2026.9.9": OLD, "v2026.9.10": OLD}
    plan = release.select_release(HEAD, tags, [published(tag) for tag in tags], TODAY)
    assert plan.tag == "v2026.9.11"


@pytest.mark.parametrize("previous", ["v2026.8.19", "v2025.12.42"])
def test_new_month_resets_the_counter(previous):
    plan = release.select_release(HEAD, {previous: OLD}, [published(previous)], TODAY)
    assert plan.tag == TAG
    assert plan.previous_tag == previous


def test_year_rollover():
    plan = release.select_release(HEAD, {TAG: OLD}, [published()], dt.date(2027, 1, 1))
    assert plan.tag == "v2027.1.0"


def test_retry_keeps_old_sha_and_version_even_after_month_rollover():
    previous, pending = "v2026.8.0", "v2026.8.1"
    plan = release.select_release(
        HEAD,
        {previous: OLD, pending: OLD, "nightly": HEAD},
        [published(previous), published(pending, draft=True)],
        TODAY,
    )
    assert (plan.tag, plan.sha, plan.previous_tag) == (pending, OLD, previous)


def test_interruption_between_tag_and_draft_is_recovered():
    plan = release.select_release(HEAD, {TAG: OLD}, [], TODAY)
    assert (plan.tag, plan.sha) == (TAG, OLD)


def test_successful_manual_tag_retry_is_a_noop():
    plan = release.select_release(HEAD, {TAG: OLD}, [published()], TODAY, TAG)
    assert not plan.publish


def test_manual_tag_targets_the_tag_not_current_main():
    plan = release.select_release(HEAD, {TAG: OLD}, [], TODAY, TAG)
    assert plan.sha == OLD


@pytest.mark.parametrize(
    "tag",
    ["nightly", "v2026.09.0", "v2026.13.0", "v2026.9.01", "v2026.9.0rc1", "v2026.9.0\n"],
)
def test_invalid_stable_tag_is_rejected(tag):
    with pytest.raises(release.ReleaseError, match="CalVer"):
        release.version_key(tag)


def test_multiple_pending_tags_require_attention():
    with pytest.raises(release.ReleaseError, match="Multiple unpublished"):
        release.select_release(HEAD, {TAG: OLD, "v2026.9.1": HEAD}, [], TODAY)


def test_future_published_tag_does_not_make_versions_go_backwards():
    tag = "v2027.1.0"
    with pytest.raises(release.ReleaseError, match="future-dated"):
        release.select_release(HEAD, {tag: OLD}, [published(tag)], TODAY)


def test_missing_published_tag_fails_closed():
    with pytest.raises(release.ReleaseError, match="no git tag"):
        release.select_release(HEAD, {}, [published()], TODAY)


def test_manual_older_version_cannot_replace_latest():
    with pytest.raises(release.ReleaseError, match="older version"):
        release.select_release(
            HEAD, {TAG: OLD, "v2026.8.0": OLD}, [published()], TODAY, "v2026.8.0"
        )


def test_prerelease_is_not_promoted_to_stable():
    with pytest.raises(release.ReleaseError, match="prerelease"):
        release.select_release(HEAD, {TAG: HEAD}, [published(prerelease=True)], TODAY)


def test_ci_must_match_the_exact_main_push():
    release.require_green_ci([ci_run()], HEAD)


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_sha": OLD},
        {"head_branch": "feature"},
        {"event": "pull_request"},
        {"status": "in_progress", "conclusion": None},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"conclusion": "skipped"},
    ],
)
def test_ineligible_ci_is_not_treated_as_green(overrides):
    with pytest.raises(release.ReleaseError, match="CI must succeed"):
        release.require_green_ci([ci_run(**overrides)], HEAD)


def test_newest_ci_run_wins_over_earlier_success():
    with pytest.raises(release.ReleaseError, match="CI must succeed"):
        release.require_green_ci([ci_run(), ci_run(id=11, conclusion="failure")], HEAD)


def test_missing_ci_fails_closed():
    with pytest.raises(release.ReleaseError, match="CI must succeed"):
        release.require_green_ci([], HEAD)


def test_plan_only_reads_and_pins_the_candidate(monkeypatch, tmp_path):
    def run(*args):
        if args == ("git", "rev-parse", "HEAD"):
            return HEAD
        if args == ("git", "tag", "--list", "v*"):
            return ""
        raise AssertionError(args)

    def api(repo, endpoint):
        assert f"head_sha={HEAD}" in endpoint
        assert endpoint.startswith("actions/workflows/ci.yml/runs?")
        return {"workflow_runs": [ci_run()]}

    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "api", api)
    monkeypatch.setattr(release, "list_releases", lambda repo: [])
    monkeypatch.setattr(release, "require_ancestor", lambda sha, main: None)
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    release.plan_release("owner/repo", "")
    assert f"sha={HEAD}\n" in output.read_text()
    assert f"automation_sha={HEAD}\n" in output.read_text()
    assert "publish=true\n" in output.read_text()


@pytest.fixture
def dist(tmp_path):
    directory = tmp_path / "dist"
    directory.mkdir()
    for name in release.filenames(TAG):
        (directory / name).write_bytes(name.encode())
    manifest = release.manifest_for(directory, TAG, HEAD)
    (directory / release.MANIFEST).write_text(json.dumps(manifest))
    return directory


def test_manifest_checks_bytes_and_commit(dist):
    release.verify_manifest(dist, TAG, HEAD)
    with pytest.raises(release.ReleaseError, match="manifest"):
        release.verify_manifest(dist, TAG, OLD)
    (dist / release.filenames(TAG)[0]).write_bytes(b"different build")
    with pytest.raises(release.ReleaseError, match="manifest"):
        release.verify_manifest(dist, TAG, HEAD)


def test_manifest_rejects_unexpected_distribution(dist):
    (dist / "extra.whl").write_bytes(b"unexpected")
    with pytest.raises(release.ReleaseError, match="Expected"):
        release.manifest_for(dist, TAG, HEAD)


def pypi_artifact(manifest, name, **overrides):
    return {
        "filename": name,
        "digests": {"sha256": manifest["sha256"][name]},
        "yanked": False,
        **overrides,
    }


def test_partial_pypi_upload_only_retries_missing_files(dist, monkeypatch, tmp_path):
    manifest = release.verify_manifest(dist, TAG, HEAD)
    wheel, sdist = release.filenames(TAG)
    monkeypatch.setattr(release, "require_current_release", lambda repo, tag: None)
    monkeypatch.setattr(release, "pypi_files", lambda tag: [pypi_artifact(manifest, wheel)])
    outputs = {}
    monkeypatch.setattr(release, "output", lambda **values: outputs.update(values))
    destination = tmp_path / "publish"
    release.prepare_pypi("owner/repo", TAG, HEAD, dist, destination)
    assert [file.name for file in destination.iterdir()] == [sdist]
    assert (destination / sdist).read_bytes() == (dist / sdist).read_bytes()
    assert outputs == {"pending": "true"}


def test_identical_pypi_artifacts_need_no_upload(dist):
    manifest = release.verify_manifest(dist, TAG, HEAD)
    assert (
        release.missing_pypi_files(
            manifest, [pypi_artifact(manifest, name) for name in release.filenames(TAG)]
        )
        == []
    )


@pytest.mark.parametrize(
    "overrides",
    [{"digests": {"sha256": "wrong"}}, {"yanked": True}, {"filename": "unexpected.whl"}],
)
def test_pypi_conflicts_are_not_silently_skipped(dist, overrides):
    manifest = release.verify_manifest(dist, TAG, HEAD)
    artifact = pypi_artifact(manifest, release.filenames(TAG)[0], **overrides)
    with pytest.raises(release.ReleaseError, match="conflicting or yanked"):
        release.missing_pypi_files(manifest, [artifact])


@pytest.mark.parametrize("status", [404, 403, 500])
def test_only_pypi_404_means_not_published(monkeypatch, status):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://pypi.org/", status, "error", {}, None)

    monkeypatch.setattr(release.urllib.request, "urlopen", fail)
    if status == 404:
        assert release.pypi_files(TAG) == []
    else:
        with pytest.raises(urllib.error.HTTPError):
            release.pypi_files(TAG)


def test_stage_uploads_the_completion_manifest_last(dist, monkeypatch):
    calls = []
    monkeypatch.setattr(release, "require_current_release", lambda repo, tag: None)
    monkeypatch.setattr(release, "remote_tag", lambda repo, tag: None)
    snapshots = iter(
        [None, published(draft=True, assets=[*release.filenames(TAG), release.MANIFEST])]
    )
    monkeypatch.setattr(release, "find_release", lambda repo, tag: next(snapshots))
    monkeypatch.setattr(release, "api", lambda *args: calls.append(args))
    monkeypatch.setattr(release, "run", lambda *args: calls.append(args))
    release.stage_release("owner/repo", TAG, HEAD, "v2026.8.0", dist)
    assert calls[0] == ("owner/repo", "git/refs", {"ref": f"refs/tags/{TAG}", "sha": HEAD})
    assert "--draft" in calls[1]
    assert calls[1][-2:] == ("--notes-start-tag", "v2026.8.0")
    assert "--clobber" in calls[2]
    assert calls[-1][-1] == str(dist / release.MANIFEST)
    assert "--clobber" not in calls[-1]


def test_stage_never_moves_an_existing_tag(dist, monkeypatch):
    monkeypatch.setattr(release, "require_current_release", lambda repo, tag: None)
    monkeypatch.setattr(release, "remote_tag", lambda repo, tag: OLD)
    with pytest.raises(release.ReleaseError, match="Refusing to move"):
        release.stage_release("owner/repo", TAG, HEAD, "", dist)


def test_completed_draft_is_reused_without_uploads(dist, monkeypatch):
    monkeypatch.setattr(release, "require_current_release", lambda repo, tag: None)
    monkeypatch.setattr(release, "remote_tag", lambda repo, tag: HEAD)
    monkeypatch.setattr(
        release,
        "find_release",
        lambda repo, tag: published(draft=True, assets=[*release.filenames(TAG), release.MANIFEST]),
    )
    manifest = release.verify_manifest(dist, TAG, HEAD)
    monkeypatch.setattr(release, "recover_assets", lambda *args: True)
    monkeypatch.setattr(release, "verify_manifest", lambda *args: manifest)
    monkeypatch.setattr(release, "run", lambda *args: pytest.fail(f"Unexpected write: {args}"))
    release.stage_release("owner/repo", TAG, HEAD, "", dist)


def test_incomplete_staged_assets_fail_instead_of_rebuilding(tmp_path, monkeypatch):
    monkeypatch.setattr(
        release,
        "find_release",
        lambda repo, tag: published(draft=True, assets=[release.MANIFEST]),
    )
    with pytest.raises(release.ReleaseError, match="asset set"):
        release.recover_assets("owner/repo", TAG, HEAD, tmp_path)


def test_interrupted_manifest_upload_is_not_a_completed_release(tmp_path, monkeypatch):
    draft = published(draft=True, assets=[*release.filenames(TAG), release.MANIFEST])
    draft["assets"][-1].update(state="starter", size=0)
    monkeypatch.setattr(release, "find_release", lambda repo, tag: draft)
    monkeypatch.setattr(release, "run", lambda *args: pytest.fail(f"Unexpected download: {args}"))
    assert not release.recover_assets("owner/repo", TAG, HEAD, tmp_path)


@pytest.mark.parametrize("has_pypi_files", [False, True])
def test_only_an_unpublished_starter_manifest_can_be_deleted(dist, monkeypatch, has_pypi_files):
    draft = published(draft=True, assets=[*release.filenames(TAG), release.MANIFEST])
    draft["assets"][-1].update(state="starter", size=0, id=123)
    snapshots = iter(
        [draft, published(draft=True, assets=[*release.filenames(TAG), release.MANIFEST])]
    )
    monkeypatch.setattr(release, "require_current_release", lambda repo, tag: None)
    monkeypatch.setattr(release, "remote_tag", lambda repo, tag: HEAD)
    monkeypatch.setattr(release, "find_release", lambda repo, tag: next(snapshots))
    monkeypatch.setattr(release, "pypi_files", lambda tag: [{}] if has_pypi_files else [])
    calls = []
    monkeypatch.setattr(release, "run", lambda *args: calls.append(args))
    if has_pypi_files:
        with pytest.raises(release.ReleaseError, match="PyPI already has files"):
            release.stage_release("owner/repo", TAG, HEAD, "", dist)
        assert not calls
    else:
        release.stage_release("owner/repo", TAG, HEAD, "", dist)
        assert calls[0] == (
            "gh",
            "api",
            "--method",
            "DELETE",
            "repos/owner/repo/releases/assets/123",
        )
        assert calls[-1][-1] == str(dist / release.MANIFEST)


def test_empty_uploaded_manifest_is_corrupt_not_a_retryable_placeholder():
    draft = published(draft=True, assets=[release.MANIFEST])
    draft["assets"][0]["size"] = 0
    with pytest.raises(release.ReleaseError, match="empty"):
        release.completed_manifest(draft)


def test_github_release_stays_draft_until_pypi_is_complete(dist, monkeypatch):
    monkeypatch.setattr(release, "pypi_files", lambda tag: [])
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(release, "run", lambda *args: pytest.fail(f"Unexpected write: {args}"))
    with pytest.raises(release.ReleaseError, match="PyPI has not confirmed"):
        release.finish_release("owner/repo", TAG, HEAD, dist)


def test_successful_finish_publishes_the_same_artifacts(dist, monkeypatch):
    manifest = release.verify_manifest(dist, TAG, HEAD)
    monkeypatch.setattr(
        release,
        "pypi_files",
        lambda tag: [pypi_artifact(manifest, name) for name in release.filenames(TAG)],
    )
    monkeypatch.setattr(release, "require_current_release", lambda repo, tag: None)
    monkeypatch.setattr(release, "remote_tag", lambda repo, tag: HEAD)
    monkeypatch.setattr(release, "recover_assets", lambda *args: True)
    monkeypatch.setattr(release, "verify_manifest", lambda *args: manifest)
    calls = []
    monkeypatch.setattr(release, "run", lambda *args: calls.append(args))
    release.finish_release("owner/repo", TAG, HEAD, dist)
    assert calls == [
        ("gh", "release", "edit", TAG, "--repo", "owner/repo", "--draft=false", "--latest")
    ]


def test_annotated_tags_resolve_to_the_commit(monkeypatch):
    monkeypatch.setattr(
        release, "run", lambda *args: f"{OLD}\trefs/tags/{TAG}\n{HEAD}\trefs/tags/{TAG}^{{}}"
    )
    assert release.remote_tag("owner/repo", TAG) == HEAD


def test_ancestry_errors_are_not_treated_as_unrelated_history(monkeypatch):
    monkeypatch.setattr(
        release.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 128)
    )
    with pytest.raises(subprocess.CalledProcessError):
        release.require_ancestor(OLD, HEAD)


def test_workflow_keeps_publish_order_and_no_approval_free_bypass():
    workflow = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert workflow["on"]["schedule"] == [{"cron": "17 1 * * *"}]
    assert workflow["on"]["push"]["tags"] == ["v*"]
    assert "dry_run" in workflow["on"]["workflow_dispatch"]["inputs"]
    assert workflow["concurrency"] == {"group": "stable-release", "cancel-in-progress": "false"}
    assert workflow["env"]["UV_FROZEN"] == "1"
    jobs = workflow["jobs"]
    assert jobs["build"]["needs"] == "plan"
    assert "!inputs.dry_run" in jobs["build"]["if"]
    build_step = next(
        step
        for step in jobs["build"]["steps"]
        if step.get("name") == "Build the exact tagged version locally"
    )
    assert "uv build --no-create-gitignore" in build_step["run"]
    assert jobs["publish"]["needs"] == ["plan", "build"]
    assert jobs["publish"]["environment"]["name"] == "pypi"
    steps = jobs["publish"]["steps"]
    assert "release.py pypi" in steps[2]["run"]
    assert steps[3]["uses"] == "pypa/gh-action-pypi-publish@release/v1"
    assert "release.py finish" in steps[4]["run"]
