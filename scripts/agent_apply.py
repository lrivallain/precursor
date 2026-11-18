"""Apply a hosted agent's JSON answer: allowlisted labels and one owned comment.

The agent job runs read-only; this script is the only step with a write token.
It accepts nothing outside the allowlists below, so an issue written to steer
the agent can at worst produce a wrong label from the list, never an action.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import agent_github as github

TYPE_LABELS = {
    "bug": "bug",
    "enhancement": "enhancement",
    "question": "question",
    "documentation": "documentation",
    "dev-experience": "dev-experience",
}
SURFACES = ("backend", "frontend", "github", "mcp", "plugins", "docs")
RISKS = ("r0", "r1", "r2")
SEVERITIES = ("blocker", "should", "nit")
VERDICTS = {
    "looks-good": "✅ Looks good",
    "concerns": "⚠️ Concerns",
    "blocking": "⛔ Blocking issues",
}
MAX_FINDINGS = 20
NEEDS_INFO = "status:needs-info"


def _text(value: Any, limit: int = 1500) -> str:
    return github.neutralize(str(value or "").strip(), limit)


def triage_labels(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Labels to add and to remove, keeping only allowlisted values."""
    add: list[str] = []
    if data.get("type") in TYPE_LABELS:
        add.append(TYPE_LABELS[data["type"]])
    add += [f"surface:{s}" for s in data.get("surface") or [] if s in SURFACES]
    remove: list[str] = []
    if data.get("risk") in RISKS:
        add.append(f"risk:{data['risk']}")
        remove = [f"risk:{r}" for r in RISKS if r != data["risk"]]
    (add if data.get("needs_info") else remove).append(NEEDS_INFO)
    return add, remove


def render_triage(data: dict[str, Any]) -> str:
    proposed = ", ".join(f"`{label}`" for label in triage_labels(data)[0]) or "none"
    lines = [
        "### Triage · `triage · hosted · claude`",
        "",
        _text(data.get("summary")),
        "",
        f"**Labels set:** {proposed}",
    ]
    dup = data.get("duplicate_of")
    if isinstance(dup, int) and not isinstance(dup, bool) and dup > 0:
        lines.append(f"**Possible duplicate of** #{dup}")
    questions = [_text(q, 500) for q in data.get("questions") or []][:5]
    if questions:
        lines += ["", "**Questions for the author:**", *[f"- {q}" for q in questions]]
    lines += [
        "",
        "_Advisory. A maintainer accepts the issue with `status:accepted` or "
        "changes any label; answering the questions re-runs triage._",
    ]
    return "\n".join(lines)


def _finding(f: dict[str, Any]) -> str:
    where = _text(f.get("path"), 200).replace("`", "")
    line = f.get("line")
    if isinstance(line, int) and line > 0:
        where = f"{where}:{line}"
    head = f"**{f['severity']}** · `{where}` · {_text(f.get('title'), 200)}"
    detail = _text(f.get("detail"), 800)
    return f"- {head}\n  {detail}" if detail else f"- {head}"


def render_review(data: dict[str, Any]) -> str:
    verdict = VERDICTS.get(data.get("verdict", ""), "⚠️ Concerns")
    findings = [f for f in data.get("findings") or [] if f.get("severity") in SEVERITIES]
    findings.sort(key=lambda f: SEVERITIES.index(f["severity"]))
    lines = [
        f"### Review · `review · hosted · claude` · {verdict}",
        "",
        _text(data.get("summary"), 2000),
    ]
    if findings:
        lines += ["", *[_finding(f) for f in findings[:MAX_FINDINGS]]]
        if len(findings) > MAX_FINDINGS:
            lines.append(f"- …and {len(findings) - MAX_FINDINGS} more")
    lines += [
        "",
        "_Advisory (phase 1 of ADR-0001): it neither approves nor blocks. Updated on every push._",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("triage", "review"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--number", type=int, required=True)
    args = parser.parse_args()

    data = json.loads(os.environ.get("AGENT_OUTPUT") or "{}")
    if not isinstance(data, dict) or not data:
        raise SystemExit("No structured output from the agent; nothing applied.")
    if args.role == "triage":
        add, remove = triage_labels(data)
        github.set_labels(args.repo, args.number, add, remove)
        github.upsert_comment(args.repo, args.number, "triage", render_triage(data))
    else:
        github.upsert_comment(args.repo, args.number, "review", render_review(data))


if __name__ == "__main__":
    main()
