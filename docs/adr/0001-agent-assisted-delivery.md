# ADR-0001: Agent-assisted delivery workflow

- **Status:** accepted, revised 2026-11-18 (hosted engine); phase 1 in place, phases 2 to 4 proposed
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
   that folder; Claude Code reads it through the `.claude/skills` symlink.
4. **Separation of duties.** The session that writes a PR never reviews or
   approves it. Reviews come from **Copilot code review** (enabled in the
   `main` ruleset, reading the `review` skill) or from another session a
   contributor runs with the same skill. The reviewer's vendor doesn't matter.
   Review loops stop after 3 rounds and escalate to a human.
5. **Autonomous runs use GitHub's native Copilot features, with no secret.**
   No external platform, no API key, no personal access token stored in the
   repository. Hosted work is limited to what Copilot does natively: code
   review now, the coding agent (assigned an issue) in phase 3. Everything
   else that needs a model, triage included, runs in sessions a contributor
   starts from their own machine (Claude Code, Copilot or anything else) with
   their own tooling and accounts; the project asks nothing of them beyond the
   shared rules and skills. Deterministic automation (labels, risk, docs
   impact, release) needs no model and stays in Actions.
6. **Gates scale with risk.** `scripts/agent_pr_classify.py` sets `risk:r0|r1|r2`
   from the changed paths (the highest file wins):
   - **R0** (docs, website, tests, issue templates): may auto-merge (phase 3).
   - **R1** (application code): a human approves the spec and merges.
   - **R2** (workflows, release, migrations, auth, telemetry, agent rules and
     skills, a raise of the quality baseline): a human reviews the diff.
7. **Untrusted text never gets write access.** No Actions job hands issue or
   PR text to a model. Workflows that need write access on fork PRs check out
   the base branch only and read the PR as data through the API.
8. **Kill switches.** `RELEASE_HOLD=true` (repository variable) makes the
   nightly release skip publishing. `AGENTS_PAUSED=true` is reserved for the
   hosted agent workflows of later phases; Copilot code review is switched off
   in the ruleset.
9. **Changelog fragments** (phase 2) replace direct edits to `[Unreleased]`, which
   conflict as soon as several agents work in parallel.
10. **Decisions are recorded as ADRs** in `docs/adr/`, listed in `AGENTS.md`.

## Rollout

| Phase | Adds | Exit criterion |
| --- | --- | --- |
| **P1 Observe** (done) | labels, risk classifier, docs-impact note, `CODEOWNERS`, `triage` and `review` skills, Copilot code review, release hold | triage labels kept unchanged ≥ 80% of the time; review findings are rarely noise |
| P2 Specify & build | spec skill and agent, `status:*` transitions, claiming with `runner:*`, changelog fragments | specs need ≤ 1 human edit on average |
| P3 Hosted & debate | Copilot coding agent on `agent:build`, contributor ↔ reviewer loop, R0 auto-merge, caps | revert rate ≤ baseline; ≤ 20% of PRs escalate |
| P4 Close the loop | ops agent (failed runs, flaky tests, telemetry reports → issues), release-notes agent, metrics | steady state |

## Consequences

- **Revision (2026-11-18).** Phase 1 first ran triage and review on GitHub
  Models with the workflow token. GitHub retired GitHub Models on 2026-07-30
  (the endpoint now answers `200 OK` with no completion), which the first live
  test (#503) revealed. Without a secret, no Actions job can call a model, so
  hosted triage was dropped and review moved to Copilot code review.
- Triage is no longer automatic: an issue waits with `needs-triage` until a
  contributor's session (or the maintainer) runs the `triage` skill.
- Copilot code review uses the account's Copilot plan and its review quota.
- With a single maintainer, `CODEOWNERS` review is requested but cannot be made
  *required*: GitHub does not let authors approve their own PRs. Revisit when
  there is a second maintainer.
- The `.claude/skills` symlink is checked out as a plain file on Windows without
  `core.symlinks`; Claude Code on Windows then does not see the skills.
- On Dependabot PRs, the classifier cannot tell a major bump from a minor one,
  so every dependency PR is R1 for now.
