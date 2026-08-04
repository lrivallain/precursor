#!/usr/bin/env python3
"""Reject lockfiles resolved through an internal package proxy.

Precursor is a public project, so `uv.lock` and the npm lockfiles must pin
public artifacts (`files.pythonhosted.org`, `registry.npmjs.org`). Contributors
on managed devices often have uv/npm pointed at a corporate mirror; re-resolving
there silently rewrites every URL and *degrades* the lockfile:

  * npm mirrors commonly re-advertise `sha1` integrity instead of `sha512`;
  * uv drops the `size` / `upload-time` provenance fields.

Both survive review as an innocuous URL diff, so this guard fails loudly
instead. Lockfile regeneration belongs in CI, which resolves on a clean network
(`.github/workflows/relock.yml`).

Run over the working tree (default) or the staged blobs (`--staged`).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:  # tomllib landed in 3.11; a git hook may run under the system python.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on the interpreter
    tomllib = None  # type: ignore[assignment]

# Hosts that indicate a package proxy rather than the public registry. Matched
# against the URL host, so a package legitimately *named* after a vendor can't
# trip the check.
PROXY_HOST_PATTERN = re.compile(
    r"""
    (?: ^ | \. )                       # host root or a subdomain boundary
    (?:
        packagefeedproxy\.microsoft\.io
      | pkgs\.visualstudio\.com
      | pkgs\.dev\.azure\.com
      | .+ \.jfrog\.io
      | artifactory .* \. .+
    )
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)

# npm lockfiles must carry SRI hashes at least as strong as sha512; corporate
# mirrors frequently downgrade these to sha1, which is collision-prone.
WEAK_INTEGRITY_PREFIXES = ("sha1-", "md5-")

UV_LOCK = "uv.lock"
NPM_LOCKS = ("frontend/package-lock.json", "website/package-lock.json")

# Paths are repo-relative so the guard behaves the same from a hook, from CI,
# or from a test run in some other working directory.
REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Findings:
    """Violations discovered in one lockfile."""

    path: str
    proxy_urls: list[str] = field(default_factory=list)
    weak_integrity: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.proxy_urls and not self.weak_integrity


def _host_of(url: str) -> str:
    """Return the host of *url*, tolerating the odd non-URL string."""
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/@]*@)?([^/:?#]+)", url)
    return match.group(2) if match else ""


def _is_proxy_url(url: str) -> bool:
    return bool(PROXY_HOST_PATTERN.search(_host_of(url)))


def staged_paths() -> set[str]:
    """Paths added/copied/modified/renamed in the index."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    return set(result.stdout.split())


def read_source(path: str, staged: bool) -> str | None:
    """Read *path* from the git index (when *staged*) or the working tree."""
    if staged:
        result = subprocess.run(
            ["git", "show", f":{path}"],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
        )
        # A non-zero status means the path isn't in the index (not staged, or
        # deleted) — nothing to validate for this commit.
        return result.stdout if result.returncode == 0 else None

    file = REPO_ROOT / path
    return file.read_text(encoding="utf-8") if file.is_file() else None


def check_uv_lock(path: str, source: str) -> Findings:
    if tomllib is None:
        return _check_uv_lock_textually(path, source)

    findings = Findings(path=path)
    data = tomllib.loads(source)

    for package in data.get("package", []):
        label = f"{package.get('name', '?')}=={package.get('version', '?')}"

        registry = (package.get("source") or {}).get("registry")
        if isinstance(registry, str) and _is_proxy_url(registry):
            findings.proxy_urls.append(f"{label} (registry) {registry}")

        artifacts = []
        sdist = package.get("sdist")
        if isinstance(sdist, dict):
            artifacts.append(sdist)
        artifacts.extend(w for w in package.get("wheels", []) if isinstance(w, dict))

        for artifact in artifacts:
            url = artifact.get("url")
            if isinstance(url, str) and _is_proxy_url(url):
                findings.proxy_urls.append(f"{label} {url}")

    return findings


def _check_uv_lock_textually(path: str, source: str) -> Findings:
    """Scan raw URLs when TOML parsing isn't available.

    Loses the package labels a structured parse gives us, but a guard only
    needs to answer "is any artifact proxied?", which the URLs alone settle.
    """
    findings = Findings(path=path)

    for number, line in enumerate(source.splitlines(), start=1):
        for url in re.findall(r'(?:url|registry)\s*=\s*"([^"]+)"', line):
            if _is_proxy_url(url):
                findings.proxy_urls.append(f"line {number}: {url}")

    return findings


def check_npm_lock(path: str, source: str) -> Findings:
    findings = Findings(path=path)
    data = json.loads(source)

    for name, entry in (data.get("packages") or {}).items():
        if not isinstance(entry, dict):
            continue
        label = name or "<root>"

        resolved = entry.get("resolved")
        if isinstance(resolved, str) and _is_proxy_url(resolved):
            findings.proxy_urls.append(f"{label} {resolved}")

        integrity = entry.get("integrity")
        if isinstance(integrity, str) and integrity.startswith(WEAK_INTEGRITY_PREFIXES):
            algorithm = integrity.split("-", 1)[0]
            findings.weak_integrity.append(f"{label} ({algorithm})")

    return findings


def report(findings: Findings, *, limit: int = 5) -> None:
    """Print a bounded summary; full lists are useless noise in a hook."""
    print(f"\n  {findings.path}")

    if findings.proxy_urls:
        print(f"    {len(findings.proxy_urls)} artifact(s) resolved via a package proxy:")
        for item in findings.proxy_urls[:limit]:
            print(f"      - {item}")
        if len(findings.proxy_urls) > limit:
            print(f"      … and {len(findings.proxy_urls) - limit} more")

    if findings.weak_integrity:
        print(f"    {len(findings.weak_integrity)} entry(ies) with weak integrity:")
        for item in findings.weak_integrity[:limit]:
            print(f"      - {item}")
        if len(findings.weak_integrity) > limit:
            print(f"      … and {len(findings.weak_integrity) - limit} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged",
        action="store_true",
        help="validate the staged blobs instead of the working tree",
    )
    args = parser.parse_args(argv)

    # A commit is only responsible for the lockfiles it touches; policing the
    # whole index would block unrelated work whenever pollution already exists
    # (CI checks the full tree).
    candidates = [UV_LOCK, *NPM_LOCKS]
    if args.staged:
        staged = staged_paths()
        candidates = [path for path in candidates if path in staged]

    results: list[Findings] = []

    for path in candidates:
        source = read_source(path, args.staged)
        if source is None:
            continue
        checker = check_uv_lock if path == UV_LOCK else check_npm_lock
        results.append(checker(path, source))

    failures = [item for item in results if not item.ok]
    if not failures:
        if not results:
            print("No lockfiles staged — nothing to check.")
            return 0
        scope = "staged" if args.staged else "working tree"
        print(f"Lockfiles OK ({len(results)} checked, {scope}) — no proxy URLs, no weak hashes.")
        return 0

    print("Lockfile check FAILED — these were resolved through a package proxy.")
    for item in failures:
        report(item)

    print(
        "\nThese lockfiles must pin public artifacts with strong hashes.\n"
        "Do not hand-edit the URLs: a proxy also downgrades npm integrity to\n"
        "sha1 and strips uv's size/upload-time, so a search-and-replace leaves\n"
        "a subtly wrong lockfile.\n\n"
        "To restore:\n"
        "    git restore uv.lock frontend/package-lock.json website/package-lock.json\n\n"
        "To change dependencies, let CI resolve on a clean network:\n"
        "    gh workflow run relock.yml --ref <your-branch>\n\n"
        "Day to day, avoid re-resolving locally:\n"
        "    export UV_FROZEN=1      # uv installs without rewriting uv.lock\n"
        "    npm ci                  # instead of `npm install`\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
