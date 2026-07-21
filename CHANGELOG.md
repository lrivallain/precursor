# Changelog

All notable changes to Precursor are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Precursor uses **CalVer** (`YYYY.M.MICRO`); the version is derived from the
latest git tag (`v<version>`) by hatch-vcs at build time. See
[RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- **Past meetings in the Live agenda picker**: the meeting lists used to *start*
  a Live session (Start screen) and to *attach* one (Context tab) now include the
  **last 7 days** of calendar meetings, not just today, so you can record or
  summarize from a meeting that already happened. Entries are split by a
  color-coded **Past** (amber) vs **Current & upcoming** (emerald) marker; the
  list auto-scrolls to that boundary so today's meetings are front-and-centre,
  and the past group is capped to the **10 most recent** meetings. Past meetings
  still spin up a session linked to the meeting (handy with **From Teams
  transcript**).

- **Summarize a Live session from the Teams transcript ("no local record")**:
  when a Teams meeting is linked to a Live session and the **WorkIQ MCP** server
  is enabled, the **Summary** tab gains a **From Teams transcript** button. It
  scrapes the meeting's published transcript through WorkIQ (Microsoft Graph:
  `/me/onlineMeetings` → `/transcripts` → VTT `/content`) and generates
  Precursor's own structured recap — so you don't need to capture the meeting
  audio locally. Best-effort and fail-closed: it requires you to be the meeting
  organizer, the delegated `OnlineMeetingTranscript.Read.All` permission, and a
  transcript that Teams has already published (a few minutes after the meeting
  ends). The Summary tab is now reachable in this case even while the session is
  still active. New endpoint `POST /api/live/{id}/summary/from-transcript`; the
  linked meeting now also carries its Teams join URL.

- **Hands-free WorkIQ re-auth**: when a WorkIQ preview session's refresh token
  ages out, Precursor now attempts the silent `prompt=none` authorization in an
  invisible iframe before showing anything. If the browser still holds a live
  Entra SSO session the session is renewed with **zero clicks** and the
  `McpAuthBanner` never appears; the manual **Sign in** banner only surfaces when
  a silent pass genuinely needs interaction (or iframe framing / third-party
  cookies block it). Gated by the new `workiq_auto_reauth_enabled` setting
  (default on); turn it off to always require the manual click.

- **In-app documentation**: the documentation site (the VitePress project in
  `website/`) is now served by the app itself at `/docs/`, reachable from a
  **Documentation** entry in the command palette and the About dialog (the About
  link opens the local `/docs/` in dev and the public site in a production
  build). In production it's pre-built with base `/docs/` (`make docs`), bundled
  into the wheel, and served statically with VitePress clean-URL resolution; in
  `precursor --dev` a live VitePress dev server runs on a hidden port that the
  SPA's Vite proxies `/docs` to, so editing any `website/**` markdown
  hot-reloads in the browser. GitHub Pages hosting is unchanged — it builds the
  same source with the default base `/` in its own workflow (`DOCS_BASE`
  selects the base). FastAPI's interactive API docs moved to `/api/docs`,
  `/api/redoc`, and `/api/openapi.json` so the root `/docs` path is free for the
  product docs.

- **Reorderable sidebar sections**: drag any section (Topics, Chats, Live,
  Agents, Files, Kanban) in the sidebar to rearrange it. Works in both the
  vertical icon rail and the horizontal tab switcher, with an insertion line
  showing exactly where the section will land (drop on either side of a
  neighbour, including the ends). The order is persisted in the browser
  (`precursor:sidebar:sectionOrder`) and shared between the two navigation
  styles; newly-shipped sections append to the end without disturbing an
  existing arrangement.

### Changed

- **Live recording is clearer to start and harder to lose**: the **Record**
  button now shows a transient **Starting…** state while it connects to Azure
  (token, SDK, capture device) instead of looking unresponsive for the couple of
  seconds before it turns red. While a recording is live, leaving the screen —
  switching cockpit, going Home, opening another live session, or jumping via
  search — now asks you to confirm (**Keep recording** / **Leave & stop
  recording**), and reloading or closing the tab triggers the browser's native
  leave prompt, so an accidental navigation no longer silently drops the capture.

- **Vertical navigation rail on the home launcher**: when the sidebar uses the
  vertical icon rail (not the horizontal tabs), that rail now also shows on the
  home launcher, so switching sections is always one click away. Home and the
  ⌘K search launcher are grouped together at the top of the rail — Home, then
  Search, then a separator, then the sections — consistent across the home rail,
  the expanded rail, and the collapsed sidebar.

### Fixed

- **WorkIQ sign-in aborted by stray loopback probes**: the OAuth callback
  server resolved on the *first* inbound connection regardless of content, so a
  favicon fetch, browser/OS connectivity probe, or pre-connect that carried no
  `code`/`error` failed the whole flow with `RuntimeError: No authorization code
  in OAuth callback` (often right after a silent `prompt=none` pass timed out).
  The loopback now answers such non-OAuth requests with `204 No Content` and
  keeps listening for the genuine redirect (or the outer timeout).

- **WorkIQ OAuth callback never returned the auth code**: the loopback
  callback's `asyncio.start_server` block was mis-indented inside the
  per-connection handler, so `_callback_handler` fell off the end and returned
  `None`. The SDK's `auth_code, state = await callback_handler()` unpack then
  crashed with `TypeError: cannot unpack non-iterable NoneType object`,
  breaking every interactive and silent WorkIQ sign-in. The server now binds
  and awaits the redirect in the handler body as intended.

- **WorkIQ sign-in 502 hid the real error behind anyio's task-group wrapper**:
  a failed interactive re-auth reached the SPA as
  `WorkIQ sign-in failed: unhandled errors in a TaskGroup (1 sub-exception)` —
  the MCP SDK's streamable-http transport raises inside a task group, so the
  actual cause (a sign-in timeout, a transport blip, a missing authorization
  code) was buried in a `BaseExceptionGroup`. The `/api/mcp/servers/workiq/reauthenticate`
  endpoint now unwraps the group (and the `__cause__`/`__context__` chain) to its
  leaf exception(s), so the 502 detail names the real reason.

- **In-app docs (`/docs/`) were silently unavailable in `precursor --dev`**:
  the live VitePress docs server only starts when `website/node_modules` is
  present, and a fresh checkout that ran `precursor --dev` without `make sync`
  first would skip it with just a log warning. The **Documentation** link then
  fell through to the SPA (or the backend's "Docs are not built" message on the
  API port). `--dev` now auto-installs the docs dependencies on first run
  (mirroring the frontend auto-build), so `/docs/` works out of the box; it
  still degrades to disabling live docs (never failing the stack) when npm is
  unavailable.

- **About dialog had two links to the same site**: the **Documentation** and
  **Website** rows in the About modal both pointed at
  `precursor.vuptime.io`. The redundant **Website** row is gone — a single
  **Documentation** link (local `/docs/` in dev, the public site in a
  production build) now covers it.

- **In-app version showed a stale dev build after the `precursor-ai` rename**:
  version resolution still queried the old `precursor` distribution name, which
  raised `PackageNotFoundError` and silently fell back to the build-time
  `_version.py` — so the **About** modal could report a stale version (e.g.
  `0.0.1.dev…`) instead of the installed/tagged one. It now resolves the
  `precursor-ai` distribution.

- **Deprecated `uuid@9` warning on frontend install**: the Azure Speech SDK
  (`microsoft-cognitiveservices-speech-sdk`) pins `uuid@^9.0.0`, which npm flags
  as no-longer-supported. A frontend `overrides` entry now forces `uuid@^14`
  (the version already resolved for the rest of the tree via mermaid), so the
  install is deprecation-free and dedupes to a single `uuid`. The SDK only uses
  `uuid.v4()`, which is unchanged across these majors.

## [2026.7.0] - 2026-07-19

### Added

- **PyPI publishing on release**: the **Release** workflow now publishes the
  wheel + sdist to [PyPI](https://pypi.org/project/precursor-ai/) on every `v*`
  tag, in addition to the GitHub Release. Publishing uses **Trusted Publishing**
  (OIDC) via a dedicated `pypi` environment — no API token to store or rotate. The
  workflow is split into `build` → (`github-release`, `pypi-publish`) jobs that
  share a single built artifact. See [RELEASING.md](RELEASING.md) for the one-time
  PyPI/GitHub environment setup.

- **PyPI distribution name is `precursor-ai`**: the plain `precursor` name was
  already taken on PyPI, so the published distribution is **`precursor-ai`**. It
  ships a matching **`precursor-ai`** command (so `uvx precursor-ai` needs no
  `--from`) plus a shorter **`precursor`** alias; the import package is unchanged.
  Install with `uv tool install precursor-ai` / `pip install precursor-ai`, or run
  it ad-hoc with `uvx precursor-ai`.

- **Website link in the About dialog**: the **About Precursor** dialog (persona
  menu) now links out to the project website at
  [precursor.vuptime.io](https://precursor.vuptime.io/), alongside the source-code
  and report-an-issue links.

- **Tool-result retention**: a new **Settings → System → Storage / retention**
  option (`tool_result_retention_days`, default `0` = keep forever) bounds
  long-term DB growth from large persisted tool outputs. Past the configured age,
  a `tool` message's `content` is replaced **in place** with a short placeholder;
  the row and its `tool_calls` metadata are preserved so conversation history
  still pairs each assistant tool-call turn with its results (no turns are
  dropped). The sweep runs best-effort on startup and periodically via a
  lightweight ticker (gated by `scheduler_enabled`); it only touches `tool` rows
  older than the cutoff whose content exceeds a small floor and isn't already the
  placeholder, so re-runs are cheap and idempotent.

- **Agent unread badges & notifications**: agent sessions now track unread
  activity just like topics and chats. When a background or scheduled agent
  produces a new reply while you aren't looking at it, its row in the Agents list
  is bold with an unread count, and — when notifications are enabled and the
  window is unfocused — a browser notification fires. Opening a session clears
  its badge (`POST /api/agents/{id}/read`); the count (assistant replies since
  `last_read_at`, exposed as `AgentSessionRead.unread_count`) is computed
  server-side from the archived event timeline. The sidebar mode switcher
  (Topics / Chats / Agents) now highlights any tab with unread items — a count
  badge when expanded, a dot on the collapsed icons and the overflow menu — and
  the browser tab title reflects the combined unread total.

- **WorkIQ preview keep-alive**: a background ticker now silently refreshes the
  WorkIQ preview OAuth token before it expires, so the hosted session survives
  without frequent interactive re-sign-in. It only acts while preview is enabled
  and a token already exists (it never starts a sign-in on its own), refreshing
  once the access token is within a margin of expiring. When the refresh token
  itself has aged out and a silent refresh can no longer proceed, it surfaces the
  existing `McpAuthBanner` re-authenticate prompt once (a tenant Conditional
  Access sign-in-frequency policy still forces periodic interactive sign-in).
  Tunable via `workiq_keepalive_enabled`, `workiq_keepalive_poll_seconds`, and
  `workiq_keepalive_refresh_margin_seconds`.

- **Lazy-loaded conversation history**: discussions no longer load their entire
  transcript up front. Topics and chats fetch the most recent page (50 messages)
  and pull older ones in as you scroll toward the top, preserving your scroll
  position. The message list endpoints (`GET /api/topics/{id}/messages` and
  `GET /api/chats/{id}/messages`) gained optional `limit` + `before_id` cursor
  params (no params still returns the full transcript). Agent timelines apply the
  same idea client-side, windowing the rendered workflow steps so very long runs
  don't mount thousands of nodes at once. Shared scroll behaviour lives in the new
  `useChatScroll` hook.

- **Skills are shared `SKILL.md` files**: skill content (name, description,
  instructions) now lives in `<copilot_home>/skills/<name>/SKILL.md` files using
  the GitHub Copilot CLI's format (YAML frontmatter + markdown body), so skills
  are interoperable with the CLI and other tools. The skills folder is detected
  the way the CLI resolves its home (`COPILOT_HOME` → `XDG_CONFIG_HOME/copilot`
  → `~/.copilot`), with a `PRECURSOR_SKILLS_DIR` override. Skills authored by
  other tools are **discovered** in the Skills tab and can be enabled per skill
  (disabled by default); enable/disable, edit, export, and delete all operate on
  the file. The `skills` table is reduced to an enablement record — if a file is
  renamed or deleted, its enablement is dropped. Pre-existing Precursor skills
  keep working as **legacy** entries and gain a **Migrate** button that writes
  the `SKILL.md` and keeps the row as an enablement record. New
  `services/skills.py` plus a name-keyed `/api/skills` (now with
  `/{name}/migrate`).
- **Memory management commands**: long-term memories can now be created, listed,
  and edited without leaving a conversation. New `/memory-store [kind] <content>`,
  `/memory-list`, and `/memory-update <id> [kind] <content>` slash commands work
  on the topic and chat surfaces (store/update also on agent sessions and headless
  scheduled topic runs; `/memory-list` surfaces the ids needed by `/memory-update`).
  The built-in `precursor` MCP server gained `store_memory` / `update_memory`
  tools — gated by a new `memory_write` capability toggle, alongside the existing
  `list_memories` read tool — so the model itself can record or refine memories.
  Memories are now also injected into **agent** sessions' system context (they
  already fed topic and chat turns), so standing preferences and facts follow you
  everywhere. Shared parsing/persistence lives in `services/memories.py`.
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

- **WorkIQ sign-in now opens in a self-closing popup.** The interactive WorkIQ
  OAuth flow used to open in a browser tab via the backend's `webbrowser.open`;
  that tab could never auto-close (browsers only let a script close a window a
  script opened), so it lingered after sign-in. The SPA now opens the sign-in in
  a script-opened popup (synchronously on click, so popup blockers don't catch
  it) and navigates it to the authorization URL the backend surfaces over the
  `/api/events` bus (`mcp.auth_url`); the loopback callback page then closes the
  popup itself once auth completes. The OS-browser path remains as a fallback for
  when no popup could be opened (`use_popup` unset on the reauth request).


  attachments (images, PDF, DOCX, PPTX) used to be stored as `LargeBinary`
  BLOBs in SQLite, which bloated the database file and made every backup/copy
  pay for the payload. They are now written as content-addressed files under
  `settings.blobs_dir` (`.precursor/blobs/<aa>/<bb>/<sha256>`, sharded like
  Git's object store); the `attachments` / `note_draft_attachments` rows keep
  only metadata plus a `sha256` pointer. Identical uploads dedupe to one file
  automatically, and a best-effort startup sweep removes blobs no row
  references. The migration spills existing BLOBs to disk before dropping the
  `data` column (and the downgrade reads them back). No API shape change — the
  attachment endpoints and schemas are unchanged.


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

- **WorkIQ preview no longer hands agents a dead token (or spams the log).** When
  the WorkIQ refresh token has aged out, the SDK's streamable-http transport
  raises our non-interactive `WorkIQAuthRequiredError` wrapped in a
  `BaseExceptionGroup` ("unhandled errors in a TaskGroup"). The narrow
  `except WorkIQAuthRequiredError` in `resolve_workiq_bearer_token` missed the
  wrapped case, so every agent attach logged a misleading
  `WorkIQ token refresh for agent attach failed` warning **and** fell back to the
  now-expired stored token — which the agent then attached and 401'd on, forcing
  yet another interactive sign-in. We now unwrap the group (reusing
  `_find_in_exception`): a genuine sign-in requirement returns `None` (skip
  attaching WorkIQ, let the keep-alive surface a single re-auth prompt) instead
  of looping on a dead bearer, while genuinely transient transport blips still
  fall back to the stored token and log once.
- **Scheduled `/guard` no longer fails open when WorkIQ needs sign-in.** A guard
  probe against a server parked in `needs_auth` used to "fail open" and run the
  scheduled turn anyway — which then errored because the headless run can't
  authenticate, and never reached the empty/non-empty check (so an empty mailbox
  was never even evaluated). The guard now distinguishes `needs_auth` from a
  transient failure: it surfaces the same inline re-authenticate prompt an
  interactive turn raises (via a new `mcp.auth_required` cross-window event that
  drives the global `McpAuthBanner`), records a durable, de-duplicated note in the
  topic transcript, and skips the run until the user signs in — the next tick
  re-probes for real.
- **A guard skip is now visible on a manual "Run now".** A manual "Run now" on a
  guarded scheduled topic still gates the run (an empty mailbox folder never
  burns an LLM turn), but the skip used to be silent, so the button appeared to
  do nothing. A manual run now records a short note (e.g. "Skipped — the WorkIQ
  guard found nothing to process, so this run didn't start") so you can see the
  gate's verdict. Automatic ticks still skip silently to avoid posting on every
  poll. The auth gate is unchanged: a guard whose server needs sign-in surfaces
  the re-authenticate prompt and skips.
- The Settings endpoint no longer returns **500 Internal Server Error** after
  signing in to the WorkIQ preview (e.g. when toggling Agents mode). The WorkIQ
  OAuth token store wrote its `issued_at` stamp as a raw ISO string into the
  shared `AppSetting` table, but that table's values are all JSON — so
  `_load_all` crashed on `json.loads` of the bare string, taking down every
  read and write of `/api/settings`. The stamp is now JSON-encoded on write and
  decoded on read (with a fallback for legacy raw rows).
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
