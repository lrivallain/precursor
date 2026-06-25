# Changelog

All notable changes to Precursor are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Precursor uses **CalVer** (`YYYY.M.MICRO`); the version is derived from the
latest git tag (`v<version>`) by hatch-vcs at build time. See
[RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- **Detach the Notes / GitHub draft panel into its own window**: the shared
  command panel (used by `/notes`, GitHub issue/comment create, and issue update
  drafts) now has a pop-out button in its header that hands the panel off to a
  separate native browser window so it can live outside the current tab while you
  keep editing. The detached window **survives navigating to another topic or
  chat** in the main app — it stays fully functional and bound to its *original*
  conversation, so every action (add to chat, add & ask AI, post comment, save
  draft, attachments, rephrase, GitHub create/update/close) still targets the
  container it was popped out from. The window mirrors the app's stylesheets and
  theme (incl. dark mode) and closes automatically once you take a terminal
  action. Closing a notes window saves the in-progress text as a recoverable
  server-side draft; GitHub draft windows discard on close. Implemented with an
  app-level host (`DetachedDraftHost`) backed by a global store
  (`detachedDraftStore`) plus self-contained controllers, rendered through a
  dedicated React root inside the popup (`DetachedWindowPortal`) so typing and
  button clicks work across the window boundary.
- **Chat description as context or system prompt**: a chat's description now
  feeds the model. By default it's injected once as discussion-level context; a
  new **"Use as system prompt"** checkbox next to the description (in chat
  settings) instead enforces it as an instruction prepended to every user turn.
  Empty descriptions are a no-op and the checkbox is disabled until you type one.
  The flag persists with the chat (`description_as_system_prompt`); a Role and a
  system-prompt description coexist deterministically (role persona in the system
  message, description enforced per turn).
- **Chats**: flat conversation sessions alongside the topic tree, reachable from
  a sidebar mode switcher (**Topics · Chats · Files**) while the persona/settings
  menu stays visible across every mode. Chats are "a topic without the tree or
  GitHub issue" — they support the full conversation toolkit: streaming replies
  with the MCP tool loop, skills and slash commands (`/rename`, `/pin`, `/unpin`,
  `/clear`, `/archive`, `/notes`), a live stats panel, dictation, message
  delete + undo, history recall, pin, archive, and unread badges. A **chat
  settings** drawer adds rename/description plus a **Promote to topic**
  transform that moves the transcript into Topics. The **Archive** view is now
  unified (Topics + Chats tabs) and restores each item into its own mode. New
  endpoints under `/api/chats/*`, `/api/chats/{id}/messages/*` (incl. `/notes`
  and `/promote`); the topic streaming generator and the stream store were both
  refactored to be container-agnostic so chats and topics share one code path.
- Chats now support **image attachments** as well (paperclip, drag-and-drop, or
  paste), matching topics — uploaded images are bound to the turn and sent to
  vision-capable models. Both message composers were unified into one shared
  `Composer` component, so topics and chats stay in lock-step.
- **`/notes` image support (topics + chats)**: Notes now accept pasted/uploaded
  images, persist them in the note draft, and show inline previews in the Notes
  pad. "Add to chat" and "Add & ask AI" both carry those images into the created
  user turn, and "Post as comment" uploads note images to GitHub attachments and
  rewrites the comment markdown to use GitHub-hosted image URLs.
- **Friendlier startup / multi-instance**: one `--port` now controls everything.
  In `--dev` the Vite UI runs on the API port **+ 1** and its `/api` proxy
  follows the backend port, so a single flag spins up a full instance. A busy
  port **auto-bumps** to the next free one (checked across IPv4 + IPv6) so
  parallel instances never collide — pass `--strict-port` to fail instead, or
  `--port 0` for an OS-assigned port. A startup banner prints the URL to open,
  `--open` launches the browser, and `--dev` auto-allows the Vite origin via
  CORS. `.env.example` is now fully optional (every setting has a built-in
  default).
- Browser notifications when an assistant turn finishes (including scheduled
  tasks) while the Precursor window isn't focused — opt-in via Settings → Chat →
  Notifications (asks for browser permission on enable). The number of unread
  messages always shows in the tab title (`(N) Precursor`), regardless of the
  notification setting.
- Multiple **LLM providers**, selectable at runtime in Settings → Model: GitHub
  Copilot, GitHub Models, **Azure AI Foundry**, OpenAI, Mistral, Hugging Face,
  Ollama, and Mock. Providers are declared in a registry
  (`services/llm/registry.py`) — adding one is a single entry plus an
  implementation. `GET /api/llm/providers` exposes each provider's config
  fields so the UI renders the right inputs (secrets redacted on read), and the
  Model panel shows discovered-model metadata (summary, context window, tags)
  with a manual model-id fallback when a provider has no catalog.
- CalVer versioning derived from git tags (hatch-vcs); a single source of truth
  replaces the previously hardcoded version literals.
- `GET /api/version` endpoint and a version line in the Settings panel.
- `version` field on `GET /api/health`.
- CI workflow (lint, format, type-check, tests, frontend build) on PRs and
  pushes to `main`.
- Release workflow: pushing a `v*` tag builds the wheel + sdist and publishes a
  GitHub Release with auto-generated notes.
- `SECURITY.md` (threat model + private vulnerability reporting) and a
  Security & deployment section in the README documenting the single-user,
  local-first, no-auth model.
- Dependabot config for pip, npm, and GitHub Actions.
- Contributor prompt helpers (`.github/prompts/`): `/ship-change` and
  `/release` workflows.
- The build-in command panels (`/notes`, `/gh-update`, `/gh-create`,
  `/gh-close`) now share a single `CommandPanel` rendered as a **floating
  window** — draggable by its header and resizable from a corner grip, with
  position and size remembered per panel. Detaching them from the chat layout
  means the scratch pad / draft cards no longer share vertical space with the
  message composer, so each is sized independently. The panels also gained a
  consistent Edit/Preview (Markdown) toggle — `/notes` previously had none.
- Speech-to-text dictation in the chat composer via **Azure AI Speech**. A mic
  button streams interim results into the draft live and appends each finalized
  phrase. Configure the resource endpoint, key, and language in Settings →
  Speech-to-text (with a "Test connection" button). The key is stored
  server-side and never returned; the browser only receives a short-lived token
  minted by the backend (`GET /api/stt/token`), and talks to Azure directly via
  the Speech SDK (lazy-loaded, so it doesn't bloat the default bundle). The mic
  is hidden when Azure isn't configured.
- Chat slash commands for topic actions: `/clear` (erase the transcript, with
  confirmation), `/archive` (archive the topic and leave it), `/rename
  <title>`, `/new <title>` (create a child topic and switch to it), and
  `/pin` / `/unpin` — quick keyboard alternatives to the existing buttons.

### Changed

- **Database schema is now managed entirely by Alembic** instead of a parallel
  hand-written backfill. `init_db` runs `alembic upgrade head` on startup, which
  both builds a fresh database and migrates an existing one (additive only —
  existing tables are never rebuilt or dropped). The dev-only column backfill /
  table-rebuild path (`_ensure_dev_columns` and friends) is gone, and the prior
  incremental migrations were squashed into a single `0001_baseline` (verified
  to reproduce the old `create_all` schema exactly). A schema change is now one
  Alembic migration that applies to dev and prod alike, and you can generate it
  from your model edits with `make migration m="…"` (autogenerate) → review →
  commit. A database stamped at a now-squashed revision is re-adopted to the
  baseline automatically on next startup (a version-row update only; no schema
  or data change), so no manual step is needed.
- **Breaking (dev/config):** the LLM provider and GitHub token are no longer
  read from the environment (`PRECURSOR_LLM_PROVIDER`, `GITHUB_TOKEN`). They now
  live in the app settings and are configured in the UI, so they can change at
  runtime without a restart. The GitHub token still falls back to your
  `gh auth login` session. The LLM provider factory is now resolved per request
  from the DB.
- `ruff` now ignores `B008` (FastAPI `Depends()` idiom); the lint gate passes
  clean across the repo.
- `mypy precursor` passes under `strict` and is a hard CI gate.
- **uv** is the documented tool for the Python env, running, building, and
  releasing (README, CONTRIBUTING, Makefile, RELEASING).
- The built wheel is now **self-contained**: a conditional build hook
  (`hatch_build.py`) bundles the SPA inside the package for distribution builds
  (not editable installs), so `uvx precursor` / `uv tool install precursor`
  serve the UI with no extra files.
- `.env.example` LLM section reconciled with `config.py` (lists all three
  providers; default `github_copilot`).
- Docs: rewrote `docs/architecture.md` to current state (scheduler, workspaces,
  command-runner jail, skills/memory, real MCP transports, three LLM providers);
  clarified GitHub token resolution (`GITHUB_TOKEN` → `gh` CLI → mock) and the
  dev-vs-prod port model (Vite `:5173` proxy → backend `:8000`).
- Frontend dependencies upgraded (Vite 8, TypeScript 6, `@vitejs/plugin-react`
  6, `react-markdown` 10, `lucide-react` 1). `lucide-react` 1 removed brand
  icons, so the GitHub mark now ships as a local `GithubIcon` component.
- Migrated the frontend to **Tailwind CSS v4** (CSS-first config): theme tokens
  moved into an `@theme` block in `index.css`, dark mode via `@custom-variant`,
  and the `@tailwindcss/vite` plugin replaces the PostCSS setup (no more
  `postcss.config.js` / `tailwind.config.js`).
- Dependabot now groups only minor/patch bumps; majors get their own PR so a
  breaking upgrade (e.g. Tailwind v4) is never bundled with safe ones.
- Unified backend logging: a single `logging.config.dictConfig` (applied at
  startup and passed to uvicorn as `log_config`) gives every record — app,
  uvicorn, and third-party (httpx, mcp, watchfiles) — one human format with an
  ISO-8601 UTC timestamp, level, and logger name. Modules now use
  `getLogger(__name__)` (no hardcoded `precursor.*` names) and operational
  `print()` calls became logger calls. App `debug` stays app-only: noisy
  libraries (aiosqlite, SQLAlchemy, sse-starlette, …) are pinned to fixed levels
  so turning on app DEBUG doesn't unleash per-statement library spam. Output is
  ANSI-coloured when stderr is a TTY and plain when piped/redirected. The
  in-tree stdio MCP servers (fetch / workspace-fs / cmd-runner / precursor)
  apply the same config in their entrypoints, so their `mcp.server` logs share
  the format instead of FastMCP's timestamp-less default; routine `mcp.client`
  connection chatter (session IDs, protocol negotiation) is quieted to WARNING.
- The topic header's GitHub status icon is now struck through with a red
  diagonal when no issue is linked, so the unlinked state reads at a glance.

### Fixed

- The favicon (and any other top-level file in the SPA build, e.g. assets Vite
  copies from `public/`) is now served in the single-process production build.
  The SPA fallback previously returned `index.html` for everything except
  `/assets/*`, so `/logo.svg` came back as HTML and the browser showed no icon;
  the fallback now serves a real file when the path maps to one inside `dist/`
  (with a traversal guard) and only returns `index.html` for client-side routes.
- Speech-to-text now releases the microphone when dictation stops. The Azure
  Speech SDK's `close()` alone left the OS mic indicator on; Precursor now owns
  the mic `MediaStream` (via `getUserMedia` + `fromStreamInput`) and stops its
  tracks on teardown.
- The `/notes` panel no longer reverts your manual edits after **Rephrase with
  AI**. The rebuilt text was re-applied on every render, so each keystroke
  snapped back to the AI version and the field looked frozen; the suggestion is
  now applied once, when the rephrase returns, leaving it editable.
- Chat errors (provider rejections, the tool-round cap, …) now **stay in the
  transcript** instead of flashing for a few seconds and vanishing. They were
  only added to the transient stream buffer, which was discarded when the
  persisted history reloaded after the stream ended; the error is now persisted
  as a system message.
- `precursor --dev` no longer prints a burst of Vite `http proxy error …
  ECONNREFUSED 127.0.0.1:8000` on startup: the Vite dev server now launches only
  once the backend port is accepting connections, instead of racing it.

- Scheduled topics now actually run: the background scheduler is started (and
  stopped) with the app lifespan. It was constructed but never started, so no
  schedule ever fired and "Run now" was a no-op.
- The `schedules` router is now registered, so `PATCH /api/schedules/{id}`
  (Save) and `POST /api/schedules/{id}/run` (Run now) work instead of returning
  `405 Method Not Allowed` (the requests were falling through to the SPA
  catch-all).

<!--
Release sections are added below by the release process, newest first, e.g.:

## [2026.6.0] - 2026-06-15

### Added
### Changed
### Fixed
### Removed
-->
