"""Model-free triage of a new issue: surface labels and possible duplicates.

No model and no secret (ADR-0001): the surface comes from the issue form's
"Surface" field, and possible duplicates from the words the title shares with
other issues. Labels a person already set are never touched; the risk class,
questions and the decision to accept stay with the `triage` skill and the
maintainer. The issue text is only parsed, never run.
"""

from __future__ import annotations

import argparse
import json
import re

import agent_github as github

# Issue-form "Surface" answers (bug_report.yml, feature_request.yml) -> label.
SURFACES = {
    "backend": "surface:backend",
    "backend (api / streaming)": "surface:backend",
    "frontend": "surface:frontend",
    "frontend (spa)": "surface:frontend",
    "github integration": "surface:github",
    "mcp": "surface:mcp",
    "plugins": "surface:plugins",
    "docs / dx": "surface:docs",
}
# Words too common in titles to say two issues are about the same thing.
# fmt: off
STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "when", "that", "this", "then",
    "than", "does", "doesn", "don't", "not", "can't", "cannot", "should", "would",
    "could", "have", "has", "had", "are", "was", "were", "been", "being", "add",
    "adds", "adding", "make", "makes", "use", "uses", "using", "support", "allow",
    "new", "more", "less", "issue", "issues", "bug", "feature", "request", "precursor",
})
# fmt: on
MIN_SCORE = 0.5
MAX_DUPLICATES = 3


def form_field(body: str, label: str) -> str | None:
    """The answer under ``### <label>`` in an issue-form body, if any."""
    match = re.search(rf"^###\s+{re.escape(label)}\s*$\n+(.+?)\s*(?=^###\s|\Z)", body, re.M | re.S)
    if not match:
        return None
    answer = match.group(1).strip()
    return None if answer in ("", "_No response_") else answer


def surface_label(body: str) -> str | None:
    answer = form_field(body, "Surface")
    return SURFACES.get(answer.lower()) if answer else None


def title_words(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", title.lower())
    return {w.strip("_-") for w in words if w not in STOP_WORDS}


def duplicates(title: str, number: int, candidates: list[dict]) -> list[dict]:
    """Issues whose title shares at least half of this title's significant words."""
    words = title_words(title)
    if len(words) < 2:
        return []
    scored = []
    for issue in candidates:
        if issue["number"] == number:
            continue
        shared = words & title_words(issue["title"])
        score = len(shared) / len(words)
        if len(shared) >= 2 and score >= MIN_SCORE:
            scored.append((score, issue))
    scored.sort(key=lambda pair: (-pair[0], -pair[1]["number"]))
    return [issue for _, issue in scored[:MAX_DUPLICATES]]


def triage_comment(found: list[dict]) -> str:
    items = "\n".join(f"- #{i['number']} ({i['state'].lower()})" for i in found)
    return (
        "**Possible duplicates** · `triage · hosted · deterministic`\n\n"
        f"These issues share most of this title's words:\n\n{items}\n\n"
        "_Found by title words only. If one of them covers this request, a "
        "maintainer closes this issue as a duplicate; otherwise ignore this note._"
    )


def search_candidates(repo: str, title: str) -> list[dict]:
    terms = sorted(title_words(title))[:8]
    if len(terms) < 2:
        return []
    # One argument per word: gh quotes an argument holding spaces as a phrase.
    query = [part for term in terms for part in ("OR", term)][1:]
    out = github.gh(
        "search", "issues", "--repo", repo, "--include-prs=false", "--limit", "30",
        "--json", "number,title,state", "--", *query,
    )  # fmt: skip
    return json.loads(out or "[]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", type=int, required=True)
    args = parser.parse_args()

    issue = github.gh_api(f"repos/{args.repo}/issues/{args.issue}")
    labels = {label["name"] for label in issue.get("labels", [])}
    surface = surface_label(issue.get("body") or "")
    if surface and not any(name.startswith("surface:") for name in labels):
        github.set_labels(args.repo, args.issue, [surface], [])

    found = duplicates(issue["title"], args.issue, search_candidates(args.repo, issue["title"]))
    github.upsert_comment(args.repo, args.issue, "triage", triage_comment(found) if found else None)
    print(
        f"#{args.issue}: surface={surface or '-'}, possible duplicates={[i['number'] for i in found]}"
    )


if __name__ == "__main__":
    main()
