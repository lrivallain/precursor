"""Move an issue's ``status:*`` label as work progresses (ADR-0001, phase 2).

No model, no secret: every transition is a rule on a GitHub event.

- A ``status:*`` label is added: it replaces any other one, and
  ``status:accepted`` clears ``needs-triage``.
- An accepted issue is assigned: ``status:in-progress``; assigning Copilot
  also adds ``runner:hosted``. Its last assignee removed: back to
  ``status:accepted``, claim labels cleared.
- A PR that closes the issue (``Closes #N``) is opened as a draft:
  ``status:in-progress``; ready for review: ``status:in-review``; closed
  without merging: back to ``status:accepted``; merged: no status left.

Labels set with the workflow token do not trigger workflows, so a transition
never cascades into another run.
"""

from __future__ import annotations

import argparse

import agent_github as github

STATUS = "status:"
NEEDS_TRIAGE = "needs-triage"
ACCEPTED = "status:accepted"
SPEC_APPROVED = "status:spec-approved"
IN_PROGRESS = "status:in-progress"
IN_REVIEW = "status:in-review"
RUNNER_HOSTED = "runner:hosted"
COPILOT_LOGINS = frozenset({"Copilot", "copilot-swe-agent"})
READY_FOR_WORK = frozenset({ACCEPTED, SPEC_APPROVED})

Change = tuple[list[str], list[str]]


def _replace_status(current: set[str], new: str | None) -> Change:
    """Make ``new`` the only status label (``None`` clears them all)."""
    remove = sorted(label for label in current if label.startswith(STATUS) and label != new)
    add = [new] if new and new not in current else []
    return add, remove


def on_labeled(label: str, current: set[str]) -> Change:
    if not label.startswith(STATUS):
        return [], []
    add, remove = _replace_status(current, label)
    if label == ACCEPTED and NEEDS_TRIAGE in current:
        remove.append(NEEDS_TRIAGE)
    return add, remove


def on_assigned(assignee: str, current: set[str]) -> Change:
    """Only an accepted issue starts on assignment: acceptance stays a human gate."""
    if not current & READY_FOR_WORK:
        return [], []
    add, remove = _replace_status(current, IN_PROGRESS)
    if assignee in COPILOT_LOGINS and RUNNER_HOSTED not in current:
        add.append(RUNNER_HOSTED)
    return add, remove


def on_unassigned(remaining: int, current: set[str]) -> Change:
    """A dropped claim makes the issue available again."""
    if remaining or IN_PROGRESS not in current:
        return [], []
    add, remove = _replace_status(current, ACCEPTED)
    return add, remove + sorted(label for label in current if label.startswith("runner:"))


def on_pull_request(action: str, draft: bool, merged: bool, current: set[str]) -> Change:
    """The transition a linked issue takes when its closing PR changes."""
    if action == "closed":
        if merged:
            return _replace_status(current, None)
        if current & {IN_PROGRESS, IN_REVIEW}:
            return _replace_status(current, ACCEPTED)
        return [], []
    if action in ("opened", "reopened", "ready_for_review", "converted_to_draft", "edited"):
        return _replace_status(current, IN_PROGRESS if draft else IN_REVIEW)
    return [], []


def labels_of(repo: str, number: int) -> set[str]:
    return {label["name"] for label in github.gh_api(f"repos/{repo}/issues/{number}/labels")}


def closing_issues(repo: str, pr: int) -> list[int]:
    owner, name = repo.split("/", 1)
    query = (
        "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
        "pullRequest(number:$n){closingIssuesReferences(first:10){nodes{number}}}}}"
    )
    data = github.gh_api_graphql(query, o=owner, r=name, n=pr)
    nodes = data["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]
    return [node["number"] for node in nodes]


def apply(repo: str, number: int, change: Change) -> None:
    add, remove = change
    if add or remove:
        github.set_labels(repo, number, add, remove)
    print(f"#{number}: +{add} -{remove}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event", choices=("labeled", "assigned", "unassigned", "pull_request"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--number", type=int, required=True, help="issue, or PR for pull_request")
    parser.add_argument("--label", default="")
    parser.add_argument("--assignee", default="")
    parser.add_argument("--remaining", type=int, default=0, help="assignees left")
    parser.add_argument("--action", default="")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--merged", action="store_true")
    args = parser.parse_args()

    if args.event == "labeled":
        apply(args.repo, args.number, on_labeled(args.label, labels_of(args.repo, args.number)))
    elif args.event == "assigned":
        apply(args.repo, args.number, on_assigned(args.assignee, labels_of(args.repo, args.number)))
    elif args.event == "unassigned":
        apply(
            args.repo, args.number, on_unassigned(args.remaining, labels_of(args.repo, args.number))
        )
    else:
        for issue in closing_issues(args.repo, args.number):
            current = labels_of(args.repo, issue)
            apply(args.repo, issue, on_pull_request(args.action, args.draft, args.merged, current))


if __name__ == "__main__":
    main()
