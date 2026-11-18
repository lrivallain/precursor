---
name: ship-change
description: Take an accepted Precursor issue (or a focused local change) to an open, green pull request - claim the issue, branch, run the quality gates, add docs and a changelog fragment, commit, open the PR, and fix CI. Use when asked to implement, fix, ship or open a PR for something in this repository.
---

# Ship a change

Drive one focused change from an issue to an open, green PR, following
`AGENTS.md`, `ARCHITECTURE.md` and `.github/copilot-instructions.md`. You open
the PR; you never merge it.

## 1. Claim the issue

Skip this step for a change without an issue.

- `gh issue view <n> --json labels,assignees` must show `status:accepted` or
  `status:spec-approved`. Anything else (`needs-triage`, `status:spec-draft`,
  `status:needs-info`, no status) means the maintainer has not cleared it:
  stop and say so.
- If it is assigned to someone else, or labelled `runner:local` /
  `runner:hosted`, another agent or person has it: stop.
- Claim it: `gh issue edit <n> --add-assignee @me --add-label runner:local`.
  The Issue status workflow then moves it to `status:in-progress`.
- If the issue has an approved spec (a comment starting `### Spec`), it is
  your acceptance criteria.

## 2. Make the change

1. **Scope.** Summarise what will change. If it mixes unrelated concerns,
   stop and propose a split.
2. **Branch** off `main`: `feat/<slug>`, `fix/<slug>`, `chore/<slug>` or
   `docs/<slug>` (an agent app may impose its own prefix; keep it).
3. **Code and tests.** Reuse the building blocks in `ARCHITECTURE.md`; new
   logic gets tests (`tests/` or `*.test.ts` next to the module).
4. **Docs.** Apply the documentation checklist in
   `.github/copilot-instructions.md`. For a user-facing change, add a
   changelog fragment (next section).
5. **Quality gates.** `make check` must pass. Fix what is red; never bypass a
   gate or raise `scripts/quality_baseline.json`.

## 3. Changelog fragment

Don't edit `CHANGELOG.md`. Add one file per user-facing change:
`changelog.d/<issue-or-slug>.<section>.md`, where `<section>` is `added`,
`changed`, `deprecated`, `removed`, `fixed` or `security`. The content is the
entry itself, one or more Markdown bullets in the changelog's voice, e.g.
`changelog.d/503.changed.md`:

```markdown
- **One shared clock helper.** ...
```

`python3 scripts/changelog_fragments.py check` validates them (CI runs it).

## 4. Commit and open the PR

1. Stage intentionally (never `.env`, `frontend/dist`, `precursor/_version.py`,
   a lockfile or the dev database) and commit with a Conventional Commits
   message: `type(scope): summary`, and a body that explains why.
2. Push and open the PR with `gh pr create`, following the PR template. Put
   `Closes #<n>` in the body so the issue moves to `status:in-review` and closes
   on merge. Open it as a draft while work remains.
3. Watch the checks (`gh pr checks`); on a failure read
   `gh run view <id> --log-failed`, fix on the same branch and push.
4. Report the PR URL, its risk label (set by the PR risk workflow) and the
   check status.

## Guardrails

- Don't force-push a shared branch, merge, tag or release.
- Keep the diff on topic; flag unrelated debt instead of fixing it here.
- If you stop before opening the PR, remove your claim:
  `gh issue edit <n> --remove-assignee @me --remove-label runner:local`.
