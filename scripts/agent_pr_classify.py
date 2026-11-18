"""Set a PR's risk class and flag missing docs, from its changed paths only.

Runs on ``pull_request_target`` from the base branch: it reads the PR's file
list through the API and never checks out or runs the PR's code. See
docs/adr/0001-agent-assisted-delivery.md for what each risk class allows.
"""

from __future__ import annotations

import argparse
import json
import os

import agent_github as github

RISK_LABELS = ("risk:r0", "risk:r1", "risk:r2")

# Agent rules and automation are R2 too: a change there alters what every
# later agent run is allowed to do.
R2_PREFIXES = (
    ".github/workflows/",
    ".github/skills/",
    ".claude/skills",
    ".claude/commands/",
    ".claude/agents/",
    "scripts/agent_",
    "precursor/backend/alembic/",
    "precursor/backend/services/telemetry/",
)
R2_FILES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        ".github/CODEOWNERS",
        ".github/copilot-instructions.md",
        ".github/labels.json",
        ".claude/settings.json",
        "scripts/release.py",
        "precursor/backend/services/github_auth.py",
        "precursor/backend/services/azure_cli.py",
        "precursor/backend/services/mcp/entra_auth.py",
        "precursor/backend/services/mcp/oauth_registry.py",
    }
)
# Lowering the ratchet baseline is routine; only a raise is R2 (see baseline_raises).
BASELINE = "scripts/quality_baseline.json"
R0_PREFIXES = ("website/", "docs/", "tests/", ".github/ISSUE_TEMPLATE/", "changelog.d/")
TEST_SUFFIXES = (".test.ts", ".test.tsx")

DOC_PREFIXES = ("website/", "docs/", "README.md", "CHANGELOG.md", "changelog.d/")
DOC_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("precursor/backend/routers/", "precursor/backend/schemas/"),
        "API routes or schemas: `website/reference/api.md`",
    ),
    (
        ("precursor/backend/config.py", ".env.example"),
        "Settings or env vars: `website/guide/configuration.md` and "
        "`website/reference/configuration.md`",
    ),
    (
        ("frontend/src/components/",),
        "UI: the feature page in `website/features/` (and both screenshot "
        "variants if a captured screen changed)",
    ),
    (("frontend/src/lib/commands.ts",), "Slash commands: the surface's feature page"),
    (("precursor/backend/services/mcp/",), "MCP: `website/features/mcp.md`"),
)


def file_risk(path: str) -> int:
    if path in R2_FILES or path.startswith(R2_PREFIXES):
        return 2
    if path.startswith(R0_PREFIXES) or path.endswith(TEST_SUFFIXES):
        return 0
    if "/" not in path and path.endswith(".md"):
        return 0
    return 1


def risk_label(paths: list[str]) -> str:
    """The highest risk among the changed files; an empty diff is routine."""
    return RISK_LABELS[max((file_risk(p) for p in paths), default=0)]


def baseline_raises(base: dict[str, dict[str, int]], head: dict[str, dict[str, int]]) -> list[str]:
    """Entries the PR adds to the debt baseline or makes bigger."""
    raised = []
    for section, counts in head.items():
        before = base.get(section, {})
        for key, value in counts.items():
            if value > before.get(key, 0):
                raised.append(f"{section}: {key} ({before.get(key, 0)} → {value})")
    return raised


def docs_hints(paths: list[str]) -> list[str]:
    """What the docs checklist asks for, when user-facing code changed without docs."""
    if any(p.startswith(DOC_PREFIXES) for p in paths):
        return []
    code = [p for p in paths if not p.endswith(TEST_SUFFIXES)]
    return [hint for prefixes, hint in DOC_RULES if any(p.startswith(prefixes) for p in code)]


def docs_comment(hints: list[str]) -> str:
    items = "\n".join(f"- {hint}" for hint in hints)
    return (
        "**Docs impact** · `docs-impact · hosted · deterministic`\n\n"
        "This PR changes user-facing code but no docs. If it changes behaviour, "
        f"update:\n\n{items}\n\n"
        "If nothing needs changing, say so in the PR description (see the "
        "documentation checklist in `.github/copilot-instructions.md`). This note "
        "goes away once the PR touches the docs."
    )


def baseline_comment(raised: list[str]) -> str:
    items = "\n".join(f"- `{entry}`" for entry in raised[:30])
    return (
        "**Quality baseline raised** · `risk · hosted · deterministic`\n\n"
        "This PR records more debt in `scripts/quality_baseline.json`, so it is "
        f"R2:\n\n{items}\n\nSplit along existing seams instead, or explain in the "
        "PR description why the limit cannot hold here."
    )


def _baseline_at(repo: str, sha: str) -> dict[str, dict[str, int]]:
    raw = github.gh(
        "api",
        "-H",
        "Accept: application/vnd.github.raw",
        f"repos/{repo}/contents/{BASELINE}?ref={sha}",
    )
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def check_baseline(repo: str, pr: int, paths: list[str]) -> list[str]:
    """Compare the baseline as data at both ends of the PR; no PR code runs."""
    if BASELINE not in paths:
        return []
    meta = github.gh_api(f"repos/{repo}/pulls/{pr}")
    try:
        base = _baseline_at(repo, meta["base"]["sha"])
        head = _baseline_at(repo, meta["head"]["sha"])
    except (json.JSONDecodeError, OSError) as exc:
        return [f"could not compare the baseline ({exc})"]
    return baseline_raises(base, head)


def changed_paths(repo: str, pr: int) -> list[str]:
    paths: list[str] = []
    for f in github.gh_paginated(f"repos/{repo}/pulls/{pr}/files"):
        paths.append(f["filename"])
        if f.get("previous_filename"):
            paths.append(f["previous_filename"])
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    args = parser.parse_args()

    paths = changed_paths(args.repo, args.pr)
    raised = check_baseline(args.repo, args.pr, paths)
    label = RISK_LABELS[2] if raised else risk_label(paths)
    github.set_labels(args.repo, args.pr, [label], [r for r in RISK_LABELS if r != label])
    hints = docs_hints(paths)
    github.upsert_comment(args.repo, args.pr, "docs-impact", docs_comment(hints) if hints else None)
    github.upsert_comment(
        args.repo, args.pr, "baseline", baseline_comment(raised) if raised else None
    )

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"PR #{args.pr}: `{label}`, {len(paths)} paths, {len(hints)} docs hints\n")


if __name__ == "__main__":
    main()
