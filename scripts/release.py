"""Plan and resume immutable stable releases. All network writes are explicit subcommands."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CALVER = re.compile(r"v([1-9][0-9]{3})\.([1-9]|1[0-2])\.(0|[1-9][0-9]*)")
MANIFEST = "release-manifest.json"


class ReleaseError(RuntimeError):
    pass


def version_key(tag: str) -> tuple[int, int, int]:
    match = CALVER.fullmatch(tag)
    if match is None:
        raise ReleaseError(f"Not a stable CalVer tag: {tag!r}")
    year, month, micro = match.groups()
    return int(year), int(month), int(micro)


@dataclass(frozen=True)
class Plan:
    tag: str
    sha: str
    previous_tag: str
    reason: str

    @property
    def publish(self) -> bool:
        return bool(self.tag)


def select_release(
    main_sha: str,
    tags: dict[str, str],
    releases: list[dict[str, Any]],
    today: dt.date,
    manual_tag: str = "",
) -> Plan:
    stable = {tag: sha for tag, sha in tags.items() if CALVER.fullmatch(tag)}
    published = [
        release["tag_name"]
        for release in releases
        if not release["draft"]
        and not release["prerelease"]
        and CALVER.fullmatch(release["tag_name"])
    ]
    previous = max(published, key=version_key, default="")
    if previous and previous not in stable:
        raise ReleaseError(f"Published release {previous} has no git tag.")
    pending = sorted(
        (tag for tag in stable if not previous or version_key(tag) > version_key(previous)),
        key=version_key,
    )
    if len(pending) > 1:
        raise ReleaseError(f"Multiple unpublished stable tags need attention: {pending}")
    if manual_tag:
        version_key(manual_tag)
        if manual_tag not in stable:
            raise ReleaseError(f"Manual tag {manual_tag} does not exist.")
        if manual_tag in published:
            return Plan("", "", previous, f"{manual_tag} is already published.")
        if previous and version_key(manual_tag) <= version_key(previous):
            raise ReleaseError("Cannot publish an older version over the latest stable release.")
        tag = manual_tag
    elif pending:
        tag = pending[0]
    elif previous and stable[previous] == main_sha:
        return Plan("", "", previous, "No commits since the last stable release.")
    else:
        month = (today.year, today.month)
        if any(version_key(tag)[:2] > month for tag in stable):
            raise ReleaseError("A future-dated stable tag would make the new version go backwards.")
        counter = max(
            (version_key(tag)[2] for tag in stable if version_key(tag)[:2] == month),
            default=-1,
        )
        tag = f"v{today.year}.{today.month}.{counter + 1}"
    if pending and tag != pending[0]:
        raise ReleaseError(f"Finish pending release {pending[0]} before publishing {tag}.")
    if any(release["tag_name"] == tag and release["prerelease"] for release in releases):
        raise ReleaseError(
            f"{tag} is marked as a prerelease; refusing to promote it automatically."
        )
    return Plan(
        tag,
        stable.get(tag, main_sha),
        previous,
        "Resume the pending release." if tag in stable else "Release unreleased main commits.",
    )


def run(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def api(repo: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
    args = ["gh", "api", f"repos/{repo}/{endpoint}"]
    if payload is None:
        return json.loads(run(*args))
    result = subprocess.run(
        [*args, "--input", "-"],
        input=json.dumps(payload),
        text=True,
        check=True,
        stdout=subprocess.PIPE,
    )
    return json.loads(result.stdout)


def list_releases(repo: str) -> list[dict[str, Any]]:
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases"))
    return [release for page in pages for release in page]


def find_release(repo: str, tag: str) -> dict[str, Any] | None:
    return next((release for release in list_releases(repo) if release["tag_name"] == tag), None)


def require_green_ci(runs: list[dict[str, Any]], sha: str) -> None:
    matching = [
        run
        for run in runs
        if run["head_sha"] == sha and run["head_branch"] == "main" and run["event"] == "push"
    ]
    latest = max(matching, key=lambda run: run["id"], default=None)
    if latest is None or latest["status"] != "completed" or latest["conclusion"] != "success":
        raise ReleaseError(f"CI must succeed on main at exactly {sha}; rerun the release after CI.")


def require_ancestor(sha: str, main_sha: str) -> None:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", sha, main_sha], check=False)
    if result.returncode == 1:
        raise ReleaseError(f"Release commit {sha} is not an ancestor of main ({main_sha}).")
    result.check_returncode()


def output(**values: str) -> None:
    for key, value in values.items():
        print(f"{key}={value}")
    if path := os.environ.get("GITHUB_OUTPUT"):
        with Path(path).open("a") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")


def plan_release(repo: str, manual_tag: str) -> None:
    main_sha = run("git", "rev-parse", "HEAD")
    tags = {
        tag: run("git", "rev-parse", f"refs/tags/{tag}^{{commit}}")
        for tag in run("git", "tag", "--list", "v*").splitlines()
        if CALVER.fullmatch(tag)
    }
    plan = select_release(
        main_sha, tags, list_releases(repo), dt.datetime.now(dt.UTC).date(), manual_tag
    )
    if plan.previous_tag:
        require_ancestor(tags[plan.previous_tag], main_sha)
    if plan.publish:
        require_ancestor(plan.sha, main_sha)
        runs = api(
            repo,
            f"actions/workflows/ci.yml/runs?branch=main&event=push&head_sha={plan.sha}&per_page=100",
        )
        require_green_ci(runs["workflow_runs"], plan.sha)
    output(
        publish=str(plan.publish).lower(),
        tag=plan.tag,
        sha=plan.sha,
        previous_tag=plan.previous_tag,
        automation_sha=main_sha,
    )
    summary = f"## Stable release\n\n{plan.reason}\n\n"
    if plan.publish:
        summary += f"Candidate: `{plan.tag}` at `{plan.sha}`.\n\n"
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a") as stream:
            stream.write(summary)


def filenames(tag: str) -> tuple[str, str]:
    version_key(tag)
    version = tag[1:]
    return f"precursor_ai-{version}-py3-none-any.whl", f"precursor_ai-{version}.tar.gz"


def manifest_for(dist: Path, tag: str, sha: str) -> dict[str, Any]:
    expected = set(filenames(tag))
    actual = {path.name for path in dist.iterdir() if path.name != MANIFEST}
    if actual != expected:
        raise ReleaseError(f"Expected {sorted(expected)}, found {sorted(actual)}.")
    hashes = {}
    for name in sorted(expected):
        with (dist / name).open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"tag": tag, "commit": sha, "sha256": hashes}


def verify_manifest(dist: Path, tag: str, sha: str) -> dict[str, Any]:
    expected = manifest_for(dist, tag, sha)
    if json.loads((dist / MANIFEST).read_text()) != expected:
        raise ReleaseError("Release artifacts do not match their immutable manifest.")
    return expected


def completed_manifest(release: dict[str, Any]) -> bool:
    marker = next((asset for asset in release["assets"] if asset["name"] == MANIFEST), None)
    if marker is None or marker["state"] == "starter":
        return False
    if marker["state"] != "uploaded" or marker["size"] <= 0:
        raise ReleaseError("The release manifest has an invalid upload state or is empty.")
    return True


def recover_assets(repo: str, tag: str, sha: str, dist: Path) -> bool:
    release = find_release(repo, tag)
    if release is None or not completed_manifest(release):
        return False
    expected = {*filenames(tag), MANIFEST}
    if {asset["name"] for asset in release["assets"]} != expected or any(
        asset["state"] != "uploaded" or asset["size"] <= 0 for asset in release["assets"]
    ):
        raise ReleaseError("The staged release has an incomplete or unexpected asset set.")
    dist.mkdir(parents=True, exist_ok=True)
    run("gh", "release", "download", tag, "--repo", repo, "--dir", str(dist))
    verify_manifest(dist, tag, sha)
    return True


def remote_tag(repo: str, tag: str) -> str | None:
    refs = run(
        "git",
        "ls-remote",
        f"https://github.com/{repo}.git",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    values = dict(line.split()[::-1] for line in refs.splitlines())
    return values.get(f"refs/tags/{tag}^{{}}") or values.get(f"refs/tags/{tag}")


def require_current_release(repo: str, tag: str) -> None:
    for release in list_releases(repo):
        other = release["tag_name"]
        if (
            not release["draft"]
            and not release["prerelease"]
            and CALVER.fullmatch(other)
            and version_key(other) > version_key(tag)
        ):
            raise ReleaseError(f"A newer stable release ({other}) already exists.")


def stage_release(repo: str, tag: str, sha: str, previous_tag: str, dist: Path) -> None:
    require_current_release(repo, tag)
    manifest = manifest_for(dist, tag, sha)
    existing_sha = remote_tag(repo, tag)
    if existing_sha is None:
        api(repo, "git/refs", {"ref": f"refs/tags/{tag}", "sha": sha})
    elif existing_sha != sha:
        raise ReleaseError(f"Refusing to move {tag} from {existing_sha} to {sha}.")
    release = find_release(repo, tag)
    if release is None:
        args = [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--verify-tag",
            "--draft",
            "--title",
            tag,
            "--generate-notes",
        ]
        if previous_tag:
            args += ["--notes-start-tag", previous_tag]
        run(*args)
    elif not release["draft"]:
        raise ReleaseError(f"{tag} is already public; refusing to modify its artifacts.")
    elif completed_manifest(release):
        with tempfile.TemporaryDirectory() as directory:
            recovered = Path(directory)
            recover_assets(repo, tag, sha, recovered)
            if verify_manifest(recovered, tag, sha) != manifest:
                raise ReleaseError("Refusing to replace previously staged release artifacts.")
        return
    else:
        for asset in release["assets"]:
            if asset["name"] == MANIFEST:
                # GitHub can leave a "starter" placeholder after a failed
                # upload. It is not a completed manifest and is safe to retry
                # only while nothing from this version has reached PyPI.
                if pypi_files(tag):
                    raise ReleaseError(
                        "PyPI already has files but the draft manifest is incomplete."
                    )
                run(
                    "gh",
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{repo}/releases/assets/{asset['id']}",
                )
    # Upload the manifest LAST; only a completed upload is the durable marker.
    # An interrupted staging attempt cannot have reached PyPI.
    run(
        "gh",
        "release",
        "upload",
        tag,
        "--repo",
        repo,
        "--clobber",
        *(str(dist / name) for name in filenames(tag)),
    )
    (dist / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    run("gh", "release", "upload", tag, "--repo", repo, str(dist / MANIFEST))
    staged = find_release(repo, tag)
    if staged is None or not completed_manifest(staged):
        raise ReleaseError(
            "Manifest upload did not complete; leave the release unpublished and retry."
        )


def pypi_files(tag: str) -> list[dict[str, Any]]:
    version_key(tag)
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/precursor-ai/{tag[1:]}/json", timeout=30
        ) as response:
            return json.load(response)["urls"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise


def missing_pypi_files(manifest: dict[str, Any], published: list[dict[str, Any]]) -> list[str]:
    expected = manifest["sha256"]
    seen = set()
    for artifact in published:
        name = artifact["filename"]
        if (
            name not in expected
            or artifact["digests"]["sha256"] != expected[name]
            or artifact["yanked"]
        ):
            raise ReleaseError(f"PyPI has a conflicting or yanked artifact: {name}")
        seen.add(name)
    return sorted(set(expected) - seen)


def prepare_pypi(repo: str, tag: str, sha: str, dist: Path, destination: Path) -> None:
    require_current_release(repo, tag)
    manifest = verify_manifest(dist, tag, sha)
    missing = missing_pypi_files(manifest, pypi_files(tag))
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ReleaseError("The PyPI upload directory must be empty.")
    for name in missing:
        shutil.copyfile(dist / name, destination / name)
    output(pending=str(bool(missing)).lower())


def finish_release(repo: str, tag: str, sha: str, dist: Path) -> None:
    manifest = verify_manifest(dist, tag, sha)
    for attempt in range(12):
        missing = missing_pypi_files(manifest, pypi_files(tag))
        if not missing:
            break
        if attempt == 11:
            raise ReleaseError(f"PyPI has not confirmed these artifacts: {missing}")
        time.sleep(10)
    require_current_release(repo, tag)
    if remote_tag(repo, tag) != sha:
        raise ReleaseError(f"The remote tag {tag} no longer points to {sha}.")
    with tempfile.TemporaryDirectory() as directory:
        recovered = Path(directory)
        if not recover_assets(repo, tag, sha, recovered):
            raise ReleaseError("No completed draft artifacts are available.")
        if verify_manifest(recovered, tag, sha) != manifest:
            raise ReleaseError("GitHub and PyPI artifacts differ.")
    run("gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest")
    url = f"https://github.com/{repo}/releases/tag/{tag}"
    print(f"Published {url}")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a") as stream:
            stream.write(f"## Published stable release\n\n[{tag}]({url}) at `{sha}`.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "recover", "stage", "pypi", "finish"])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--tag", default="")
    parser.add_argument("--sha", default="")
    parser.add_argument("--previous-tag", default="")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--upload-dir", type=Path, default=Path("publish-dist"))
    args = parser.parse_args()
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    if args.command == "plan":
        plan_release(args.repo, args.tag)
        return
    version_key(args.tag)
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("--sha must be a full commit SHA")
    if args.command == "recover":
        output(restored=str(recover_assets(args.repo, args.tag, args.sha, args.dist)).lower())
    elif args.command == "stage":
        stage_release(args.repo, args.tag, args.sha, args.previous_tag, args.dist)
    elif args.command == "pypi":
        prepare_pypi(args.repo, args.tag, args.sha, args.dist, args.upload_dir)
    else:
        finish_release(args.repo, args.tag, args.sha, args.dist)


if __name__ == "__main__":
    main()
