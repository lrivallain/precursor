---
name: changelog
description: Fold the changelog fragments in changelog.d/ into CHANGELOG.md's [Unreleased] section and open the PR. Use when asked to update, assemble or tidy the changelog, or before preparing release notes.
---

# Assemble the changelog

Contributors don't edit `CHANGELOG.md`: each PR adds a fragment in
`changelog.d/` (see `changelog.d/README.md`). This role folds them in, so the
changelog stays readable without merge conflicts between parallel PRs.

## Steps

1. Branch off an up-to-date `main`: `chore/changelog-<date>`.
2. `python3 scripts/changelog_fragments.py check`, then
   `python3 scripts/changelog_fragments.py assemble`. It adds each fragment
   under the matching `### Added` / `### Changed` / ... heading of
   `[Unreleased]` (creating the heading when missing) and deletes the
   fragment files.
3. Read the result. Merge entries that describe the same change, put the most
   significant first in each heading, keep the changelog's voice. Don't rewrite
   entries that were already in `[Unreleased]`.
4. Commit (`docs(changelog): assemble fragments`) and open the PR. It is
   `risk:r0`.

## Guardrails

- Don't move `[Unreleased]` into a version section here; version sections are
  written when a release is prepared, and GitHub Release notes remain the exact
  per-version history.
- Don't edit fragments that belong to open PRs: only `main` is assembled.
