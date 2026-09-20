---
mode: agent
description: Run the autonomous CalVer release pipeline or preview its candidate.
---

# Release Precursor

Stable releases run nightly at 01:17 UTC. To release sooner, dispatch the same
workflow on `main`. The version is derived from its immutable CalVer tag by
hatch-vcs; there is no version literal or changelog-promotion commit to make.
Full reference: `RELEASING.md`.

## Steps

1. Preview with `gh workflow run release.yml --ref main -f dry_run=true` and
   inspect that run's summary. It identifies the exact candidate and requires
   successful main-push CI on that SHA without creating a tag.
2. Unless the user requested only a preview, dispatch
   `gh workflow run release.yml --ref main`. Capture the new run's ID and watch
   that specific run with `gh run watch <id>`.
3. Read the run summary. It either reports no unreleased changes or identifies
   the released tag and URL. Confirm that the GitHub Release is public with
   wheel, sdist and manifest assets, and report the release URL.

## Guardrails

- Do not check out or push commits to `main`, promote `[Unreleased]`, manually
  bump a version, or bypass CI.
- The release planner owns version allocation. If a tag was reserved by an
  interrupted run, it resumes that tag/SHA and the same staged artifacts first.
- Do not move/delete stable tags or overwrite completed draft artifacts.
  Existing PyPI files must match the manifest; a conflict needs investigation,
  not `skip-existing`.
- A pending CI run must finish before releasing; a failed one must be fixed.
- PyPI uses OIDC through `release.yml` and the `pypi` environment. Unattended
  publication requires the owner to remove environment approval requirements.
- Publishing does not update or restart any running Precursor instance.
