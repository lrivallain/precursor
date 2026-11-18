# ADR-0001: Agent-assisted delivery workflow

- **Status:** accepted; phase 1 in place, phases 2 to 4 proposed
- **Date:** 2026-11-18
- **Visual:** [`docs/agentic-workflow.canvas.html`](../agentic-workflow.canvas.html)
  (open it in a browser: flow, state machine, roster, risk classes)

## Context

Most of Precursor's code, tests and docs are already written by agents (Claude
Code, GitHub Copilot) working from a maintainer's machine. A human still turns
each ask into a branch, reviews every PR and merges. Releases already run
without anyone: `release.yml` publishes every night, gated on CI for the exact
`main` commit. So **merging is the ship decision**.

We want more of the cycle to run on its own (issues, specs, PRs, review, docs,
changelog) and still be able to run each task from the Claude app, the Copilot
app or a GitHub-hosted runner.

## Decision

1. **GitHub is the bus.** Workflow state lives in labels (`status:*`, `risk:*`,
   `agent:*`, `runner:*`, `surface:*`, defined in `.github/labels.json`). A
   Project board may show them; it never holds state.
2. **One rules entry point.** [`AGENTS.md`](../../AGENTS.md) is read by every
   agent; `CLAUDE.md` and `.github/copilot-instructions.md` stay as imports and
   tool-specific notes.
3. **Roles are agent skills.** Each role is one `SKILL.md` under
   `.github/skills/<role>/`. Copilot (cloud agent, CLI, IDE and code review) reads
   that folder; Claude Code reads it through the `.claude/skills` symlink. Hosted
   workflows point the engine at the same file.
4. **Separation of duties.** The run that writes a PR never reviews or
   approves it: every same-repo PR gets a separate, read-only review run on
   GitHub Models (`AGENT_MODEL`, default `openai/gpt-4.1`) following the
   `review` skill. The reviewer's vendor doesn't matter. Review loops stop
   after 3 rounds and escalate to a human.
5. **Autonomous runs use GitHub only.** Hosted agents run on GitHub: Copilot
   (code review now, the coding agent in phase 3) and GitHub Models, called
   with the workflow's own `GITHUB_TOKEN` (`models: read`). No external
   platform, no API key, no secret. Sessions a contributor runs from their own
   machine (Claude Code, Copilot or anything else) use that contributor's own
   tooling and accounts; the project asks nothing of them beyond the shared
   rules and skills.
6. **Gates scale with risk.** `scripts/agent_pr_classify.py` sets `risk:r0|r1|r2`
   from the changed paths (the highest file wins):
   - **R0** (docs, website, tests, issue templates): may auto-merge (phase 3).
   - **R1** (application code): a human approves the spec and merges.
   - **R2** (workflows, release, migrations, auth, telemetry, agent rules and
     skills, a raise of the quality baseline): a human reviews the diff.
7. **Untrusted text never gets write access.** Hosted agent jobs are read-only
   and return JSON validated against a schema. A separate deterministic job
   applies allowlisted labels and posts comments with @-mentions neutralised.
   Workflows that need write access on fork PRs check out the base branch only.
8. **Kill switches.** The repository variable `AGENTS_PAUSED=true` stops every
   hosted agent; `RELEASE_HOLD=true` makes the nightly release skip publishing.
9. **Changelog fragments** (phase 2) replace direct edits to `[Unreleased]`, which
   conflict as soon as several agents work in parallel.
10. **Decisions are recorded as ADRs** in `docs/adr/`, listed in `AGENTS.md`.

## Rollout

| Phase | Adds | Exit criterion |
| --- | --- | --- |
| **P1 Observe** (done) | labels, risk classifier, docs-impact note, `CODEOWNERS`, triage agent (labels + comment), advisory reviewer, kill switches | triage labels kept unchanged ≥ 80% of the time; review findings are rarely noise |
| P2 Specify & build | spec skill and agent, `status:*` transitions, claiming with `runner:*`, changelog fragments | specs need ≤ 1 human edit on average |
| P3 Hosted & debate | hosted contributor on `agent:build`, contributor ↔ reviewer loop, R0 auto-merge, caps | revert rate ≤ baseline; ≤ 20% of PRs escalate |
| P4 Close the loop | ops agent (failed runs, flaky tests, telemetry reports → issues), release-notes agent, metrics | steady state |

## Consequences

- Hosted agents need no secret. On GitHub Models the model has **no tools**:
  the workflow fetches the issue or diff and passes it as data, which also
  keeps injected text away from any capability. Reviews only see the diff
  (cut to `AGENT_MAX_INPUT_CHARS`, 24,000 by default, to fit GitHub Models'
  per-request input limit) and say when they are partial.
- GitHub Models' free rate limits apply (a few dozen requests a day on the
  larger models). A refused call (403 when Models is disabled, 429 when the
  limit is reached) posts nothing and leaves a warning in the run summary.
- Copilot code review is optional. If it is enabled in the repository rules,
  it reads the same `.github/skills/review` skill.
- With a single maintainer, `CODEOWNERS` review is requested but cannot be made
  *required*: GitHub does not let authors approve their own PRs. Revisit when
  there is a second maintainer.
- The `.claude/skills` symlink is checked out as a plain file on Windows without
  `core.symlinks`; Claude Code on Windows then does not see the skills.
- On Dependabot PRs, the classifier cannot tell a major bump from a minor one,
  so every dependency PR is R1 for now.
