<!--
Thanks for contributing to Precursor! Please fill out the sections below.
-->

## Summary

<!-- One or two sentences describing what this PR does and why. -->

## Linked issue

Closes #

## Changes

-

## Verification

- [ ] `make check` passes (lint, types, tests, quality ratchet, API types,
      frontend typecheck, unit tests and build)
- [ ] New logic has tests (`tests/` for the backend, `*.test.ts` for the frontend)
- [ ] Manually exercised the affected flow

## Reuse

- [ ] Checked [`ARCHITECTURE.md`](../ARCHITECTURE.md) for an existing building
      block before adding a helper, store, hook, background job or MCP tool
- [ ] No copy-pasted logic: shared code lives in the shared place, and new
      building blocks are added to `ARCHITECTURE.md`
- [ ] API changes: schema has a `response_model`, `make api-types` was run
- [ ] Quality baseline not raised (if `scripts/quality_baseline.json` changed,
      it only went down, or the reason is explained below)

## Notes for reviewers

<!-- Anything reviewers should know: tradeoffs, follow-ups, screenshots. -->
