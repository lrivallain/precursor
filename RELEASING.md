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

## Cutting a release

1. Make sure `main` is green (CI passes) and `CHANGELOG.md` `[Unreleased]`
   captures what's shipping.
2. Pick the next CalVer per the policy above. Verify what the build would
   produce locally:

   ```bash
   uv version    # or: uv run python -c "from precursor import __version__; print(__version__)"
   ```

3. Promote the `[Unreleased]` changelog section to a dated release heading and
   commit it:

   ```markdown
   ## [2026.6.0] - 2026-06-15
   ```

4. Tag and push (the **leading `v`** is required — the release workflow triggers
   on `v*`):

   ```bash
   git tag v2026.6.0
   git push origin v2026.6.0
   ```

5. The **Release** workflow (`.github/workflows/release.yml`) then:
   - builds the frontend and bundles it into the wheel,
   - runs `uv build` (hatch-vcs stamps the version from the tag),
   - verifies the built version matches the tag,
   - creates a GitHub Release with the wheel + sdist and auto-generated notes,
   - **publishes the wheel + sdist to [PyPI](https://pypi.org/project/precursor/)**
     via [Trusted Publishing](#pypi-trusted-publishing-one-time-setup) (OIDC — no
     API token).

## PyPI Trusted Publishing (one-time setup)

Publishing uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OpenID Connect), so there is **no API token** to store or rotate. Wire it up once:

1. Create the project's trusted publisher on PyPI (**Your projects → precursor →
   Publishing**, or **Publishing** on your account before the first upload). Use:
   - **Owner**: `lrivallain`
   - **Repository**: `precursor`
   - **Workflow name**: `release.yml`
   - **Environment**: `pypi`
2. In this repo, add a GitHub **Environment** named `pypi`
   (**Settings → Environments → New environment**). Optionally add required
   reviewers so a human approves each publish.

The `pypi-publish` job requests an `id-token` and runs in the `pypi` environment;
those two values must match the publisher configured on PyPI.

## Verifying a build locally

```bash
make wheel          # builds the SPA, then `uv build` → dist/*.whl + *.tar.gz
# or by hand:
cd frontend && npm ci && npm run build && cd ..
uv build
```

The built filename encodes the resolved version. A clean checkout on the tag
yields exactly `precursor-2026.6.0-...`. The wheel is **self-contained**: the SPA
is bundled inside the package (`precursor/frontend_dist/`), so an installed build
serves the UI with no extra files:

```bash
uvx precursor                 # run the published wheel directly
uv tool install precursor     # or install the `precursor` command
```

## Notes & known follow-ups

- **Commit messages** follow Conventional Commits (see `CONTRIBUTING.md`); the
  GitHub Release notes are generated from them.
- **PyPI**: each tagged release publishes the wheel + sdist to PyPI via Trusted
  Publishing (OIDC) — see [the setup above](#pypi-trusted-publishing-one-time-setup).
  The GitHub Release ships the same artifacts as attached assets.
