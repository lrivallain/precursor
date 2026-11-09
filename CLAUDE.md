# Precursor: notes for coding agents

Read these first; they are the project's own rules, shared with human
contributors:

@ARCHITECTURE.md
@.github/copilot-instructions.md

Key points that are easy to get wrong:

- **Reuse before writing.** Check the building-block tables in
  `ARCHITECTURE.md` before adding a helper, store, hook, ticker, sweep or MCP
  tool. If one almost fits, extend it.
- **Stay under the limits.** `make ratchet` fails when a file or function gets
  bigger or more complex than its recorded baseline, or when new code crosses a
  limit. Split along existing seams instead of raising the baseline. When you
  improved things, run `make ratchet-update` and commit the baseline.
- **API types are generated.** After changing a Pydantic schema or a route's
  `response_model`, run `make api-types` and commit
  `frontend/src/lib/generated/apiTypes.ts`. Never edit it by hand.
- **Lockfiles are resolved by CI only** (`.github/workflows/relock.yml`). Run
  `uv` with `UV_FROZEN=1` (the Makefile sets it) and install frontend packages
  with `npm ci`. If you change dependencies, commit the manifest only and let
  the Relock workflow regenerate the lockfile.
- **Definition of done:** `make check` passes (lint, types, tests, ratchet, API
  types, frontend typecheck, unit tests and build), docs are updated for
  user-facing changes, and new logic has tests (`tests/` for the backend,
  `*.test.ts` next to the module for the frontend).
