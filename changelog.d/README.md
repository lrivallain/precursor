# Changelog fragments

Don't edit `CHANGELOG.md` in a PR: add one file here per user-facing change, so
parallel PRs never conflict on the changelog (ADR-0001).

- **Name:** `<issue-or-slug>.<section>.md`, where `<section>` is `added`,
  `changed`, `deprecated`, `removed`, `fixed` or `security`
  (e.g. `503.changed.md`, `canvas-export.added.md`).
- **Content:** the entry itself, one or more Markdown bullets in the
  changelog's voice: `- **Short title.** What changed for the user.`
- **Check:** `python3 scripts/changelog_fragments.py check` (run by CI and
  `make check`).

The `changelog` skill folds the fragments into `CHANGELOG.md`'s `[Unreleased]`
section from time to time (`python3 scripts/changelog_fragments.py assemble`)
and deletes them.
