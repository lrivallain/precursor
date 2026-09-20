---
title: Releasing
---

# Releasing Precursor

Precursor ships from **git tags**. The version is **CalVer** — `YYYY.M.MICRO` —
resolved automatically by [hatch-vcs](https://github.com/ofek/hatch-vcs) from the
latest `v<version>` tag. **There is no version literal to bump by hand.**

## Versioning policy — CalVer `YYYY.M.MICRO`

- `YYYY` — four-digit year of the release.
- `M` — month, **no leading zero** (`6`, not `06`).
- `MICRO` — release counter **within that month**, starting at `0`; resets to `0`
  on the first release of a new month.

Examples: first June 2026 release `2026.6.0`, a follow-up the same month
`2026.6.1`, the first July release `2026.7.0`. The format is valid under both PEP
440 (Python) and semver (npm), sorts chronologically, and is human-readable.
Untagged/dev builds get a suffix, e.g. `2026.6.1.dev3+g0f3ad9f.d20260615`.

## Autonomous stable releases

Every night at **01:17 UTC** (02:17/03:17 in Paris), the **Release** workflow
ships unreleased changes from `main`. Merging into `main` is the ship decision;
unmerged branches are never released. Documentation and dependency changes
are eligible too.

1. Capture the exact `main` commit and compare it with the last successful
   stable release, ignoring the rolling `nightly` tag. No changes means no tag.
2. Require a successful **CI** push run on `main` for that exact commit. Missing,
   pending, cancelled or failing CI stops the release.
3. Choose the next CalVer and build the wheel and sdist, including the SPA and
   in-app documentation. uv runs with `UV_FROZEN=1`.
4. Exercise a fresh Python 3.12 wheel installation outside the checkout, with
   newly resolved dependencies: installed version, startup, migrations in a
   disposable database, API, SPA and docs. No real credentials or Node.js
   runtime are needed.
5. Create the immutable tag and a **draft** GitHub Release. Stage the original
   artifacts and their SHA-256 manifest, publish them to PyPI via OIDC, then
   make the GitHub Release public and **latest**.

The comparison spans all commits since the last successful release, not just
"today", so missed runs do not lose changes. GitHub's scheduler is best-effort:
runs may start late and inactive public repositories may have schedules disabled.

The automation does not push commits to `main` or require a PAT. Tag creation
and publishing run in the same workflow because a tag pushed by `GITHUB_TOKEN`
does not start another push-triggered workflow.

### Release now or preview

Use **Actions → Release → Run workflow** on **main**. Check **dry_run** for a
planning-only run: it computes the candidate and enforces the CI gate but
does not build, tag or publish.

```bash
gh workflow run release.yml --ref main -f dry_run=true
gh workflow run release.yml --ref main
```

Manual `v<version>` tag pushes still work, with the same CI and ancestry gates.
Tags must use canonical CalVer and point to a commit with successful main-push
CI. Prefer the workflow button to avoid choosing the counter yourself.

### Notes and recovery

**GitHub Release notes are the per-version history**, generated from merged PRs
since the previous stable tag. Contributors continue updating the changelog's
`[Unreleased]` development notes and feature documentation in their PRs. The
release does not promote that section or require a changelog commit.

All stable runs share one concurrency group. Once a tag is reserved, retries
finish that tag and commit before releasing newer changes. A completed draft's
artifacts are downloaded, not rebuilt. `release-manifest.json` records the full
commit and SHA-256 hashes; it is uploaded last as the staging completion marker.
An unfinished manifest upload can be retried before any files reach PyPI,
but a completed manifest is never replaced.

Partial PyPI uploads resume with only the missing files. Already-uploaded files
must match the manifest; hash conflicts, yanked files and multiple unfinished
stable tags stop the run for maintainer attention. A GitHub finalization failure
after PyPI publication is also recoverable by rerunning. Do not delete/move
stable tags or overwrite completed draft assets to force a retry.

Failures surface in Actions and its workflow notifications. Fix CI and rerun,
or let the next scheduled run retry. A bad published release needs a new version.

The rolling `nightly` prerelease still follows every push independently.
Publishing a release does not automatically update running installations or
change the installer's default rolling channel.

## PyPI Trusted Publishing (one-time setup)

Publishing uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OpenID Connect), so there is **no API token** to store or rotate. Configure it
once:

1. On PyPI, create a trusted publisher for the **`precursor-ai`** project (the
   plain `precursor` name is already taken) — **PyPI Project Name** `precursor-ai`,
   **Owner** `lrivallain`, **Repository** `precursor`, **Workflow** `release.yml`,
   **Environment** `pypi`.
2. In this repo, add a GitHub **Environment** named `pypi`
   (**Settings → Environments**). **Remove required reviewers and wait timers**
   for unattended releases. Deployment restrictions, if enabled, must allow
   both `main` (scheduled/manual runs) and `v*` tags (manual tag runs).

The `publish` job runs in the `pypi` environment and requests an
`id-token` — both must match the publisher registered on PyPI.
The workflow name remains `release.yml`; tag rules must allow it to create
`v*` tags. It does not need a bypass of `main`'s branch protection.

## Verifying a build locally

```bash
make wheel          # builds the SPA, then `uv build` → dist/*.whl + *.tar.gz
# or by hand:
export UV_FROZEN=1
npm --prefix frontend ci && npm --prefix frontend run build
npm --prefix website ci && DOCS_BASE=/docs/ npm --prefix website run docs:build
uv build
```

The built filename encodes the resolved version (the `precursor-ai` distribution
normalises to `precursor_ai` in the filename). The wheel is **self-contained**:
the SPA is bundled inside the package (`precursor/frontend_dist/`), so an installed
build serves the UI with no extra files:

```bash
uvx precursor-ai              # run the published wheel directly
uv tool install precursor-ai  # or install the `precursor-ai` command
```

## Notes

- **Commit messages** follow Conventional Commits — the GitHub Release notes are
  generated from them.
- **PyPI**: each tagged release publishes the wheel + sdist to PyPI via Trusted
  Publishing (OIDC) — see the one-time setup above. The GitHub Release ships the
  same artifacts as attached assets.
- **Distribution name**: the PyPI project is `precursor-ai`; it ships a matching
  `precursor-ai` command (so `uvx precursor-ai` needs no `--from`) plus a
  `precursor` alias, and the import package is unchanged.
