# Releasing Precursor

Precursor ships from **git tags**. The version is **CalVer** — `YYYY.M.MICRO` —
and is resolved automatically by [hatch-vcs](https://github.com/ofek/hatch-vcs)
from the latest `v<version>` tag. There is no version literal to bump by hand.

## Versioning policy (CalVer `YYYY.M.MICRO`)

- `YYYY` — four-digit year of the release.
- `M` — month, **no leading zero** (`6`, not `06`).
- `MICRO` — release counter **within that month**, starting at `0`; it resets
  to `0` on the first release of a new month.

Examples: first June 2026 release `2026.6.0`, a follow-up the same month
`2026.6.1`, the first July release `2026.7.0`.

This format is valid under both PEP 440 (Python) and semver (npm), sorts
chronologically, and is human-readable. Untagged/dev builds get a suffix, e.g.
`2026.6.1.dev3+g0f3ad9f.d20260615`.

## Autonomous stable releases

The **Release** workflow (`.github/workflows/release.yml`) runs every night at
**01:17 UTC** (02:17 in Paris in winter, 03:17 in summer). Merging into `main`
is the ship decision: all unreleased commits are eligible, including docs and
dependency changes. Nothing is released from unmerged branches.

Each run:

1. Captures an immutable `main` SHA and compares it with the last published
   stable release. No new commits means no new version, even in a new month.
   The moving `nightly` tag is ignored.
2. Requires the latest **CI** push run on `main` for that exact SHA to have
   completed successfully. Missing, pending, cancelled or failing CI stops the
   release; it never falls back to a different green revision.
3. Chooses the next CalVer, builds the frontend **and in-app docs**, and builds
   the wheel and sdist with the intended tag locally. All uv commands run with
   `UV_FROZEN=1`.
4. Installs the wheel into a fresh Python 3.12 environment with freshly resolved
   runtime dependencies, outside the checkout. Checks the installed version,
   startup/migrations against a disposable SQLite database, API, SPA and docs,
   without real credentials or a Node.js runtime.
5. Creates the immutable remote tag and a **draft** GitHub Release. Uploads the
   wheel and sdist, then a `release-manifest.json` containing their SHA-256
   hashes and the full source commit.
6. Publishes the artifacts to PyPI using OIDC. Only after PyPI confirms both
   files and their hashes does the GitHub Release become public and **latest**.

Eligibility is based on commits since the last successful stable release, not
the previous calendar day. Missed schedules or failed releases therefore do not
lose changes. GitHub schedules are best-effort and can run late; inactive public
repositories may have their schedules disabled by GitHub.

One concurrency group serializes scheduled releases, manual runs and manual
tags. The workflow never pushes commits to protected `main`. It creates tags
with `GITHUB_TOKEN` and continues publishing in the **same workflow**: a tag
created by that token does not trigger another push workflow. No PAT is needed.

### Run now or preview

In **Actions → Release → Run workflow**, select **main**. Leave `dry_run`
unchecked to release now, or check it to compute the candidate and enforce the
CI gate without building, tagging or publishing.

```bash
gh workflow run release.yml --ref main -f dry_run=true
gh workflow run release.yml --ref main
```

Manual `v<version>` tags still trigger the same gated pipeline. They must point
to a commit on `main` with a successful main-push CI run, use canonical CalVer,
and not supersede an unfinished release. Prefer **Run workflow** so the
version counter is chosen automatically.

### Release notes and changelog

GitHub Release notes, generated from merged PRs since the previous stable tag,
are the **per-version history**. Contributors still add development notes to
`CHANGELOG.md` under `[Unreleased]` and update relevant docs in their PR.
Release automation does **not** promote that section, rewrite the changelog,
or require a release-preparation commit or approval PR. Existing dated
changelog sections remain as historical records.

### Failures and retries

The failed Actions run reports the failing step; use GitHub's workflow failure
notifications for alerts. A CI failure creates no automatic tag. Fix `main`
and let the next nightly run pick it up, or run the release manually.

Once a tag is reserved, the next run resumes that tag and SHA **before** any
newer changes, even across a month boundary. After staging completes, retries
download the original draft artifacts rather than rebuilding the same version.
The manifest is uploaded last as a durable completion marker.
An unfinished GitHub upload placeholder is retried only before that version
has any files on PyPI; a completed manifest is never replaced.

For a partial PyPI upload, only missing files are uploaded. Existing files must
match the manifest exactly; conflicting hashes or yanked files stop the run.
If PyPI succeeded but finalizing GitHub failed, rerunning finishes the same
release. Newer `main` changes then ship on a subsequent run. Multiple pending
stable tags require manual attention rather than silently choosing one.

Never delete/move published stable tags, overwrite a completed draft's assets,
or delete its manifest to force a retry. A bad published release needs a new
CalVer version. Hash conflicts or a deliberately withdrawn pending release
need maintainer investigation; the workflow fails closed.

## PyPI Trusted Publishing (one-time setup)

Publishing uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OpenID Connect), so there is **no API token** to store or rotate. Wire it up once:

1. Create the project's trusted publisher on PyPI for the **`precursor-ai`**
   project (**Your projects → precursor-ai → Publishing**, or **Publishing** on
   your account as a *pending* publisher before the first upload). Use:
   - **PyPI Project Name**: `precursor-ai`
   - **Owner**: `lrivallain`
   - **Repository**: `precursor`
   - **Workflow name**: `release.yml`
   - **Environment**: `pypi`
2. In this repo, add a GitHub **Environment** named `pypi`
   (**Settings → Environments → New environment**). For autonomous releases,
   **remove required reviewers and wait timers**. If deployment branch/tag
   restrictions are enabled, allow both `main` (scheduled/manual runs) and
   `v*` tags (manual tag runs).

This repository publishes one distribution, `precursor-ai`. Plugins —
`precursor-kanban` included — live in their own repositories and set up their
own publisher there.

The `publish` job requests an `id-token` and runs in the `pypi` environment;
those two values must match the publisher configured on PyPI.
Keep the trusted publisher's workflow name as `release.yml`. The repository's
tag rules must allow this workflow to create `v*` tags; no bypass of `main`'s
branch protection is needed.

## Verifying a build locally

```bash
make wheel          # builds the SPA, then `uv build` → dist/*.whl + *.tar.gz
# or by hand:
export UV_FROZEN=1
npm --prefix frontend ci && npm --prefix frontend run build
npm --prefix website ci && DOCS_BASE=/docs/ npm --prefix website run docs:build
uv build
```

The built filename encodes the resolved version. A clean checkout on the tag
yields exactly `precursor_ai-2026.6.0-...` (the `precursor-ai` distribution
normalises to `precursor_ai` in the filename). The wheel is **self-contained**:
the SPA is bundled inside the package (`precursor/frontend_dist/`), so an
installed build serves the UI with no extra files:

```bash
uvx precursor-ai              # run the published wheel directly
uv tool install precursor-ai  # or install the `precursor-ai` command
```

## The nightly channel

Tagged releases are the *stable* channel. Alongside them, `nightly.yml`
publishes a **rolling prerelease of `main` on every push** — the same wheel and
sdist, attached to a permanently-named `nightly` tag that is replaced each run.

It exists to remove the last reason to run Precursor from a source checkout: the
published wheel already carries the SPA and the in-app docs, so following `main`
needs no clone and no Node.js.

Alongside the artifacts it uploads a small `version.json`:

```json
{
  "channel": "nightly",
  "version": "2026.7.1.dev229",
  "commit": "679a4fe6e",
  "wheel_url": "https://github.com/…/precursor_ai-….whl",
  "extra_wheel_urls": []
}
```

That manifest is what `precursor service check` reads — one request, rather than
listing release assets and guessing which is the host wheel. `extra_wheel_urls`
carries any companion wheel built alongside the host on the same run, pinned to
that commit so a nightly is never paired with something stale from PyPI. This
repository builds only the host — plugins release from their own repositories —
so the list is empty today; the field stays because the manifest declares it and
a client must keep honouring it.

Two consequences worth knowing:

- **Dev versions aren't ordered** — two branches can share a base version — so
  the nightly channel compares the **commit**, not the version number. Stable
  compares versions normally.
- The release is **deleted and recreated** each run (`--cleanup-tag`) so the tag
  follows `main` and no stale wheel is left for a client to resolve.

The rolling channel remains independent from scheduled stable releases; the
nightly tag is never promoted or used as a release candidate. Neither pipeline
automatically installs updates on running instances. The installer continues to
default to the rolling channel; tagged installations normally follow stable.

## Notes & known follow-ups

- **Dependency floors vs. the published wheel.** `uv.lock` pins exact versions
  for dev and CI, but **end users of the wheel resolve fresh** and never see the
  lockfile. Coarse `>=major.minor` floors are right for compatible releases; an
  API-breaking major needs a real cap in `pyproject.toml` (see the `mcp<3` pin,
  and the 2026.9.1 candidate a too-wide `mcp` bound stopped at the smoke gate).
  The backend CI job alone does *not* prove a fresh `uvx precursor-ai` works;
  the **Fresh wheel install** CI job is the one that resolves like a user does.
- **Commit messages** follow Conventional Commits (see `CONTRIBUTING.md`); the
  GitHub Release notes are generated from them.
- **PyPI**: each tagged release publishes the wheel + sdist to PyPI via Trusted
  Publishing (OIDC) — see [the setup above](#pypi-trusted-publishing-one-time-setup).
  The GitHub Release ships the same artifacts as attached assets.
- **Distribution name**: the PyPI project is `precursor-ai` (plain `precursor` is
  taken). It ships a matching `precursor-ai` command — so `uvx precursor-ai` needs
  no `--from` — plus a `precursor` alias; the import package is unchanged.
