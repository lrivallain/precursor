#!/usr/bin/env python3
"""Tell CI whether a pull request can affect the backend checks.

The backend jobs (lint, types, the suite on Linux and Windows) are the slowest
checks, and a PR that only touches the SPA or the docs site can't change their
outcome. Those jobs still run and report, because they are required checks, but
they skip their steps when this prints ``backend=false``.

The rule errs towards running: a path skips only if it is listed as something
the backend never reads. Any path this file doesn't know about runs the suite.
``tests/test_ci_backend_scope.py`` checks the exceptions still exist and that
the tests don't read other files from the skippable trees.

Writes ``backend=true|false`` to ``$GITHUB_OUTPUT`` when set; prints it either
way. Only pull requests are ever scoped: a push to ``main`` always runs.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Trees the backend never imports, serves from source, or tests against...
SKIPPABLE_PREFIXES = ("website/", "frontend/", ".impeccable/")
# ...except these files, which backend tests do read.
BACKEND_READS = frozenset(
    {
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/components/SettingsPanel.tsx",
    }
)
# Markdown is prose everywhere except where code or fixtures live.
MARKDOWN_STAYS_BACKEND = ("precursor/", "tests/", "docs/examples/", "docs/schemas/")


def affects_backend(path: str) -> bool:
    if path in BACKEND_READS:
        return True
    if path.startswith(SKIPPABLE_PREFIXES):
        return False
    return not (path.endswith(".md") and not path.startswith(MARKDOWN_STAYS_BACKEND))


def changed_files() -> list[str]:
    """Files a pull request's merge commit changes relative to its base.

    ``actions/checkout`` checks out ``refs/pull/N/merge``, whose first parent is
    the base branch: diffing against it is exactly what merging would change.
    ``--no-renames`` lists both sides of a move, so moving a file *out* of the
    backend still counts as a backend change.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def decide() -> tuple[bool, str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return True, "not a pull request"
    try:
        files = changed_files()
    except (OSError, subprocess.CalledProcessError) as exc:
        return True, f"could not list the changed files ({exc})"
    hits = [f for f in files if affects_backend(f)]
    if hits:
        return True, f"{len(hits)} backend-relevant file(s), e.g. {hits[0]}"
    return False, f"all {len(files)} changed file(s) are frontend/docs only"


def main() -> int:
    backend, reason = decide()
    line = f"backend={'true' if backend else 'false'}"
    print(f"{line} ({reason})")
    if not backend:
        print(f"::notice title=Backend checks skipped::{reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
