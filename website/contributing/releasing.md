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

## Cutting a release

1. Make sure `main` is green and `CHANGELOG.md`'s `[Unreleased]` section captures
   what's shipping.
2. Pick the next CalVer per the policy above, and verify what the build would
   produce:

   ```bash
   uv version    # or: uv run python -c "from precursor import __version__; print(__version__)"
   ```

3. Promote the `[Unreleased]` changelog section to a dated release heading and
   commit it:

   ```markdown
   ## [2026.6.0] - 2026-06-15
   ```

4. Tag and push — the **leading `v`** is required (the release workflow triggers
   on `v*`):

   ```bash
   git tag v2026.6.0
   git push origin v2026.6.0
   ```

5. The **Release** workflow (`.github/workflows/release.yml`) then:
   - builds the frontend and bundles it into the wheel,
   - runs `uv build` (hatch-vcs stamps the version from the tag),
   - verifies the built version matches the tag,
   - creates a GitHub Release with the wheel + sdist and auto-generated notes, and
   - **publishes the wheel + sdist to [PyPI](https://pypi.org/project/precursor-ai/)**
     via Trusted Publishing (OIDC — no API token).

## PyPI Trusted Publishing (one-time setup)

Publishing uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OpenID Connect), so there is **no API token** to store or rotate. Configure it
once:

1. On PyPI, create a trusted publisher for the **`precursor-ai`** project (the
   plain `precursor` name is already taken) — **PyPI Project Name** `precursor-ai`,
   **Owner** `lrivallain`, **Repository** `precursor`, **Workflow** `release.yml`,
   **Environment** `pypi`.
2. In this repo, add a GitHub **Environment** named `pypi`
   (**Settings → Environments**); optionally require a reviewer to approve each
   publish.

The `pypi-publish` job runs in the `pypi` environment and requests an
`id-token` — both must match the publisher registered on PyPI.

## Verifying a build locally

```bash
make wheel          # builds the SPA, then `uv build` → dist/*.whl + *.tar.gz
# or by hand:
cd frontend && npm ci && npm run build && cd ..
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
