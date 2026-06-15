---
mode: agent
description: Cut a CalVer release — pick the version, promote the changelog, tag, push, and verify the GitHub Release.
---

# Tag a release

Cut a CalVer release of Precursor. The version is **derived from the git tag**
by hatch-vcs — there is no literal to bump. Full reference: `RELEASING.md`.

## Preconditions

- `main` is the source of truth and CI is green on it. Confirm with
  `git switch main && git pull` and `gh run list --branch main --limit 1`.
- The working tree is clean.

## Steps

1. **Pick the version (CalVer `YYYY.M.MICRO`).**
   - `YYYY` = current year, `M` = current month **without leading zero**,
     `MICRO` = release counter **within that month**, starting at `0` and
     resetting to `0` on the first release of a new month.
   - Inspect existing tags (`git tag --list 'v*' | sort -V`) to choose the next
     value. Example: after `v2026.6.0` in June → `v2026.6.1`; first July release
     → `v2026.7.0`.
2. **Promote the changelog.** In `CHANGELOG.md`, rename the `[Unreleased]`
   section to a dated release heading — `## [<version>] - YYYY-MM-DD` — and open
   a fresh empty `[Unreleased]` above it. Commit on `main` (or via a quick PR if
   `main` is protected): `docs(changelog): release <version>`.
3. **Sanity-check the build version.** `uv build --wheel` and confirm the wheel
   filename encodes the intended version (or `uvx hatch version`). A clean
   checkout on the tag must yield exactly `<version>`.
4. **Tag and push.** The leading `v` is required (the release workflow triggers
   on `v*`):
   ```bash
   git tag v<version>
   git push origin v<version>
   ```
5. **Verify the release.** Watch `release.yml`
   (`gh run watch` on the tag's run). On success, confirm the GitHub Release
   exists with the wheel + sdist assets and auto-generated notes
   (`gh release view v<version>`). Report the release URL.

## Guardrails

- Never edit a version literal — if you find one, that's a bug; flag it.
- Don't delete or move a tag once pushed (it's published). To fix a bad release,
  cut a new patch (`MICRO + 1`).
- If the build's version doesn't match the tag, stop — the release job's own
  guard will fail; investigate hatch-vcs / git history (shallow clone? missing
  tags?) before retrying.
