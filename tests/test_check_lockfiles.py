"""Tests for the lockfile purity guard (scripts/check_lockfiles.py).

The guard is what stops a corporate package mirror's rewritten URLs and
downgraded hashes from reaching the repository, so its detection rules are
worth pinning down.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_lockfiles.py"

PROXY_TARBALL = (
    "https://ms-feed-2.pkgs.visualstudio.com/1es-public/_packaging/"
    "npm-public/npm/registry/left-pad/-/left-pad-1.3.0.tgz"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_lockfiles", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()


def _npm_lock(resolved: str, integrity: str) -> str:
    return json.dumps(
        {"packages": {"node_modules/left-pad": {"resolved": resolved, "integrity": integrity}}}
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
        "https://files.pythonhosted.org/packages/aa/left_pad-1.3.0.tar.gz",
    ],
)
def test_public_urls_are_accepted(url: str) -> None:
    assert guard._is_proxy_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        PROXY_TARBALL,
        "https://packagefeedproxy.microsoft.io/pypi/simple",
        "https://pkgs.dev.azure.com/org/_packaging/feed/npm/registry/x/-/x-1.0.0.tgz",
    ],
)
def test_proxy_urls_are_rejected(url: str) -> None:
    assert guard._is_proxy_url(url) is True


def test_package_named_after_a_vendor_is_not_flagged() -> None:
    """Detection keys on the URL host, so the package name must not matter."""
    url = "https://registry.npmjs.org/@azure/core-util/-/core-util-1.13.1.tgz"
    assert guard._is_proxy_url(url) is False


def test_clean_npm_lock_has_no_findings() -> None:
    source = _npm_lock("https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz", "sha512-AAAA")
    assert guard.check_npm_lock("frontend/package-lock.json", source).ok


def test_proxy_npm_lock_is_flagged() -> None:
    findings = guard.check_npm_lock(
        "frontend/package-lock.json", _npm_lock(PROXY_TARBALL, "sha1-A")
    )
    assert not findings.ok
    assert len(findings.proxy_urls) == 1
    assert len(findings.weak_integrity) == 1


def test_weak_integrity_is_flagged_even_on_a_public_url() -> None:
    """A mirror can hand back a public-looking URL with a downgraded hash."""
    source = _npm_lock("https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz", "sha1-AAAA")
    findings = guard.check_npm_lock("frontend/package-lock.json", source)
    assert not findings.ok
    assert findings.proxy_urls == []
    assert len(findings.weak_integrity) == 1


def test_link_entries_without_integrity_are_ignored() -> None:
    """Workspace links and bundled deps legitimately carry no hash."""
    source = json.dumps(
        {"packages": {"node_modules/local": {"resolved": "../local", "link": True}}}
    )
    assert guard.check_npm_lock("frontend/package-lock.json", source).ok


CLEAN_UV_LOCK = """
[[package]]
name = "idna"
version = "3.10"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/aa/idna-3.10.tar.gz", hash = "sha256:a" }
"""

PROXY_UV_LOCK = """
[[package]]
name = "idna"
version = "3.10"
source = { registry = "https://packagefeedproxy.microsoft.io/pypi/simple" }
sdist = { url = "https://packagefeedproxy.microsoft.io/pypi/files/idna-3.10.tar.gz", hash = "sha256:a" }
"""


def test_clean_uv_lock_has_no_findings() -> None:
    assert guard.check_uv_lock("uv.lock", CLEAN_UV_LOCK).ok


def test_proxy_uv_lock_flags_registry_and_artifact() -> None:
    findings = guard.check_uv_lock("uv.lock", PROXY_UV_LOCK)
    assert not findings.ok
    assert len(findings.proxy_urls) == 2


def test_uv_lock_text_fallback_matches_the_parsed_result() -> None:
    """The hook may run under a python without tomllib; both paths must agree."""
    assert guard._check_uv_lock_textually("uv.lock", CLEAN_UV_LOCK).ok
    assert not guard._check_uv_lock_textually("uv.lock", PROXY_UV_LOCK).ok


def test_repository_lockfiles_are_clean() -> None:
    """The guard is only worth having if the tracked lockfiles satisfy it."""
    assert guard.main([]) == 0, "run the Relock workflow to regenerate the lockfiles"
