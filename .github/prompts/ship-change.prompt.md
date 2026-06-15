---
mode: agent
description: Ship a focused code change — run gates, branch, conventional commit, open a PR, and confirm CI.
---

# Ship a code change

Drive a code change from working tree to an open, green PR. Follow the repo
conventions in `.github/copilot-instructions.md` and `CONTRIBUTING.md`.

## Steps

1. **Scope check.** Summarize what changed (`git status`, `git diff --stat`).
   If the change mixes unrelated concerns, stop and ask whether to split it.
2. **Quality gates.** Run `make check` (ruff, ruff format --check, mypy,
   pytest, frontend typecheck + build). Fix anything red before continuing —
   never commit with failing gates, and don't bypass them.
3. **Update docs/changelog if user-facing.** If the change affects behavior,
   add a bullet to the `[Unreleased]` section of `CHANGELOG.md`. If it changes
   an API, update both the Pydantic schema and `frontend/src/lib/types.ts`.
4. **Branch.** Create a branch off `main` named for the change:
   `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, or `docs/<slug>`.
5. **Commit.** Stage intentionally (never `.env`, `frontend/dist`,
   `precursor/_version.py`, or the dev DB — verify with `git status`). Use a
   **Conventional Commits** message: `type(scope): summary`, with a body
   explaining the *why*.
6. **Push & PR.** Push the branch and open a PR with `gh pr create` using the
   repo PR template; reference the issue with `Closes #N` when applicable. If
   `main` isn't on the remote yet, push it first.
7. **Confirm CI.** Watch the PR checks (`gh pr checks`); if a check fails, fetch
   the log (`gh run view --log-failed`), fix on the same branch, and push again.
   Report the PR URL and final check status.

## Guardrails

- Do not force-push, merge, or tag as part of this workflow — only open the PR.
- Keep the diff minimal and on-topic; no drive-by refactors.
- If a gate reveals pre-existing repo-wide debt unrelated to the change, flag it
  rather than fixing it here.
