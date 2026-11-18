---
title: Releasing
---

# Releasing Precursor

Releases are **automatic**: merging into `main` is the ship decision. This page
summarizes
[`RELEASING.md`](https://github.com/lrivallain/precursor/blob/main/RELEASING.md),
which has the recovery rules and one-time setup in full.

| Channel | Workflow | When | Published as |
| --- | --- | --- | --- |
| **Stable** | `release.yml` | nightly at **01:17 UTC**, if `main` has unreleased commits and green CI | `v<CalVer>` tag, GitHub Release (**latest**), PyPI `precursor-ai` |
| **Nightly** | `nightly.yml` | after each successful CI run on a `main` push | rolling `nightly` prerelease + `version.json` |

## Versioning

**CalVer `YYYY.M.MICRO`**, resolved from the latest `v<version>` tag by
[hatch-vcs](https://github.com/ofek/hatch-vcs); there is no literal to bump.
`M` has no leading zero; `MICRO` counts releases within the month from `0`
(`2026.6.0`, `2026.6.1`, `2026.7.0`). Untagged builds get a dev suffix
(`2026.6.1.dev3+g0f3ad9f.d20260615`).

## Stable releases

Each scheduled run:

1. Takes the exact `main` SHA and compares it with the last stable release
   (the `nightly` tag is ignored). No new commits, no release.
2. Requires a **successful CI push run on `main` for that SHA**. Missing,
   pending, cancelled or failed CI stops the run.
3. Picks the next CalVer and builds the frontend, in-app docs, wheel and sdist
   (`UV_FROZEN=1`).
4. Installs the wheel into a fresh Python 3.12 env with freshly resolved
   dependencies and smoke-tests version, startup, migrations, API, SPA and docs.
5. Creates the tag and a **draft** GitHub Release with the artifacts and a
   `release-manifest.json` (SHA-256 + commit).
6. Publishes to PyPI (Trusted Publishing, OIDC), then makes the GitHub Release
   public and latest.

Missed or failed runs lose nothing: the next run takes every commit since the
last successful release. GitHub Release notes, generated from merged PRs, are
the per-version history; `CHANGELOG.md` `[Unreleased]` is still kept current in
PRs but isn't promoted by the release.

```bash
gh workflow run release.yml --ref main -f dry_run=true   # plan + CI gate only
gh workflow run release.yml --ref main                   # release now
```

Manual `v<version>` tags run the same gated pipeline; prefer the workflow so the
counter is chosen for you.

To pause releases, set the repository variable `RELEASE_HOLD=true`
(**Settings → Secrets and variables → Actions → Variables**). Runs then
publish nothing and say so in their summary; delete the variable to resume.

::: warning Recovery
Runs are serialized and resumable: a reserved tag is finished before newer
changes ship, and retries reuse the draft's artifacts. Never delete or move a
published tag, overwrite a completed draft's assets, or delete its manifest. A
bad release needs a new version. Details:
[Failures and retries](https://github.com/lrivallain/precursor/blob/main/RELEASING.md#failures-and-retries).
:::

## Rolling Nightly builds

`nightly.yml` listens for completed **CI** runs and publishes only for a
successful push run on this repository's `main`, at that exact tested SHA. It
replaces the `nightly` release (moving, never deleting, the tag) and uploads:

```json
{
  "channel": "nightly",
  "version": "2026.7.1.dev229",
  "commit": "679a4fe6e",
  "wheel_url": "https://github.com/…/precursor_ai-….whl",
  "extra_wheel_urls": []
}
```

`precursor service check` reads that `version.json`. The nightly channel
compares **commits**, not versions. Superseded commits are skipped, so an older
green commit never replaces a newer one. Retry with
`gh workflow run nightly.yml --ref main` (still CI-gated). `pages.yml` deploys
the website independently.

## Building locally

```bash
make wheel        # SPA + docs, then uv build → dist/*.whl + *.tar.gz
```

The wheel is self-contained (SPA in `precursor/frontend_dist/`):

```bash
uvx precursor-ai              # run the published wheel
uv tool install precursor-ai  # install the `precursor-ai` command (+ `precursor` alias)
```

The PyPI project is `precursor-ai` (the import package is `precursor`). PyPI
publishing uses Trusted Publishing from `release.yml` in the `pypi` environment;
see [the one-time setup](https://github.com/lrivallain/precursor/blob/main/RELEASING.md#pypi-trusted-publishing-one-time-setup).
