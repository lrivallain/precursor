---
title: Development workflow
---

# Development workflow

## Branch, commit, PR

1. Open (or claim) an issue.
2. Branch from `main`: `git checkout -b feat/short-description`.
3. Keep commits focused, with [Conventional Commits](https://www.conventionalcommits.org/)
   prefixes (`feat:`, `fix:`, `chore:`, `docs:`); release notes are generated
   from them.
4. Open a PR from the template; reference the issue with `Closes #N`.

Merging into `main` is the ship decision: the next
[nightly release](/contributing/releasing) picks it up.

## Agents in the workflow

Agents (Claude Code, GitHub Copilot, and hosted agents in GitHub Actions) do
much of the work; humans decide at a few gates. The design and its rollout are
in [ADR-0001](https://github.com/lrivallain/precursor/blob/main/docs/adr/0001-agent-assisted-delivery.md);
the rules every agent follows are in
[`AGENTS.md`](https://github.com/lrivallain/precursor/blob/main/AGENTS.md).

**Labels carry the state** (`.github/labels.json`, synced by the Labels
workflow): `status:*` for the stage, `risk:r0|r1|r2` for the gate, `surface:*`
for the area, `agent:*` to opt an issue in to a hosted agent.

**What runs today (phase 1):**

| Workflow | When | What it does |
| --- | --- | --- |
| PR risk & docs impact | every PR | sets `risk:r0` (docs, website, tests), `risk:r1` (application code) or `risk:r2` (workflows, release, migrations, auth, telemetry, agent rules and skills, or a raise of the quality baseline) from the changed paths; comments when user-facing code changed without docs |
| Agent · triage | new issue, or the author answering a `status:needs-info` question | proposes type, surface and risk labels, flags duplicates, asks up to 3 questions. A maintainer accepts with `status:accepted` |
| Agent · review | PRs from same-repo branches not named `claude/*` | an advisory Claude review: one comment with `blocker` / `should` / `nit` findings, updated on each push. It never approves or blocks |
| Copilot code review | PRs from `claude/*` branches | the other vendor reviews Claude's work, using the same `review` skill |

The roles are skills in `.github/skills/` (`triage`, `review`), so you can run
the same role locally: ask Claude Code or Copilot to "triage issue 123" or
"review PR 456".

**Safety.** Hosted agent jobs are read-only and answer in JSON; a separate
step applies only allowlisted labels and posts the comment. R2 paths request
the maintainer's review through `CODEOWNERS`.

**Setup and switches** (repository settings):

- Secret `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` for the hosted
  Claude agents. Without one they skip and say so in the run summary.
- Copilot code review: **Settings → Rules → Rulesets → Automatically request
  Copilot code review** for `main`.
- Variable `AGENTS_PAUSED=true` stops every hosted agent.
- Variable `RELEASE_HOLD=true` makes the nightly release publish nothing.

## Database migrations

Alembic is the single source of truth for the schema; startup runs
`alembic upgrade head` for dev and prod alike.

```bash
make migration m="add foo to chats"   # autogenerate from the model change
# review precursor/backend/alembic/versions/<new>.py, then commit it
make migrate                          # optional: apply to your local DB now
```

One migration per change. Check column type changes, server defaults and data
migrations by hand.

## Continuous integration

`.github/workflows/ci.yml` runs on every PR and `main` push. All jobs must pass.

| Job | Checks |
| --- | --- |
| Lockfiles | every artifact pinned to a public registry with a strong hash |
| Quality ratchet | complexity, size, duplication, untyped responses; API types up to date |
| Backend | `uv sync --locked`, ruff, ruff format and mypy in one job; `pytest -n 8 --dist loadgroup` split over 2 shards |
| Backend on Windows | the same pytest run, split over 4 shards |
| Fresh wheel install | builds the wheel, installs it with the `kanban` extra **without** the lockfile (as `uvx precursor-ai` would), runs the release smoke gate and `scripts/smoke_mcp.py kanban` |
| Install on Windows | `install.ps1`, login item, service lifecycle, self-update |
| Frontend | `npm ci`, typecheck, Vitest, build |
| Docs site | `npm run docs:build` for `website/` |

Backend steps are skipped (but report success) when a PR only touches
`frontend/`, `website/` or Markdown. If a backend test reads a frontend file,
add it to `BACKEND_READS` in `scripts/ci_backend_scope.py`
(`tests/test_ci_backend_scope.py` enforces it). Tests sharing a fixed resource
(e.g. a port) use `pytest.mark.xdist_group`.

The backend jobs are sharded: `PRECURSOR_TEST_SHARD=k/n` makes `conftest.py`
keep one slice of the suite, split by a hash of each test id (an
`xdist_group` stays in one shard). The check names above are small jobs that
pass when every shard does, so adding shards never changes the required
checks. Reproduce a CI shard locally with
`PRECURSOR_TEST_SHARD=2/4 uv run pytest -n 8 --dist loadgroup`.

## Lockfiles

`uv.lock` and both `package-lock.json` files pin **public** artifacts
(`files.pythonhosted.org`, `registry.npmjs.org`) with strong hashes.
**Regenerating them is CI's job.** Corporate mirrors weaken them (`sha1`
integrity, missing uv provenance), so never commit a locally re-resolved
lockfile; revert it.

| Task | Command |
| --- | --- |
| Install from lockfiles | `make sync` (`npm ci`, `UV_FROZEN=1`) |
| Block bad lockfiles at commit | `make hooks` (once); `make lockcheck` on demand |
| Change a dependency | edit `pyproject.toml` / `package.json`, commit, then `gh workflow run relock.yml --ref "$(git branch --show-current)"` |
| Need the new package locally first | `UV_FROZEN=0 uv lock && uv sync && git restore uv.lock` |
| `npm ci` 404s on a mirror | `npm --prefix website install --no-package-lock` (leaves the lockfile untouched) |

The **Relock** workflow regenerates the lockfiles, verifies install and build,
and pushes to your branch (or opens a PR against `main`). Dependabot updates
arrive the same way.

## Adding a plugin

Plugins live in their own repositories and register via
`[project.entry-points."precursor.plugins"]`. See the
[plugin reference](/reference/plugins) and, to list one,
[Submitting a plugin](/plugins/submitting).

## Documentation

Docs ship **in the same PR** as the change. The decision checklist (feature
page, landing grid, configuration reference, API reference, `CHANGELOG.md`
`[Unreleased]`, screenshots) is in
[`.github/copilot-instructions.md`](https://github.com/lrivallain/precursor/blob/main/.github/copilot-instructions.md#documentation).

| Where | What |
| --- | --- |
| `website/` | this VitePress site: GitHub Pages via `pages.yml` on `main` pushes, and in-app at [`/docs/`](/reference/api#serving-the-docs-in-app) |
| `README.md`, `CONTRIBUTING.md`, `RELEASING.md`, `ARCHITECTURE.md` | repository front door and contributor rules |
| `docs/` | in-repo design notes and JSON schemas |

```bash
cd website
npm ci
npm run docs:dev        # live reload (precursor --dev also serves it at /docs/)
npm run docs:build      # must pass before you open the PR
```

### Screenshots

Screenshots in `website/public/screenshots/` come in pairs (`foo.png`,
`foo-dark.png`); retake both when a UI change alters one. Capture them from the
seeded demo (`scripts/seed_demo.py`, `scripts/demo_server.sh`,
`scripts/capture_screenshots.js`) so the persona reads "Guest / Not connected"
and no config warnings show. The full loop is in
[`.github/copilot-instructions.md`](https://github.com/lrivallain/precursor/blob/main/.github/copilot-instructions.md#screenshots).
