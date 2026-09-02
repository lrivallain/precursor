# Changelog

All notable changes to Precursor are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Precursor uses **CalVer** (`YYYY.M.MICRO`); the version is derived from the
latest git tag (`v<version>`) by hatch-vcs at build time. See
[RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- **Three more Agent 365 MCP servers — `workiq-planner`, `workiq-word` and
  `workiq-excel` — on the sign-in you already have.** Precursor shipped two of
  Microsoft's hosted [Agent 365](https://precursor.vuptime.io/features/mcp.html#agent-365-workiq-teams-and-workiq-user)
  endpoints; it now ships the five that a single credential reaches. `workiq-planner`
  covers plans, tasks and goals; `workiq-word` and `workiq-excel` create a
  document or workbook, read its content, and add or reply to comments.

  **They cost no extra sign-in.** All five authenticate as the same Entra client
  against the same resource, so the shared Agent 365 token is accepted verbatim —
  if Teams or User already works, the three new servers do too, with nothing to
  configure. Enable them in **Settings → MCP** like any other built-in.

  Agent 365's *other* endpoints (`mcp_ProductivityServer`, `mcp_MailServer`,
  `mcp_FilesServer`, …) are deliberately not included: they sit behind a second
  Entra resource and reject the shared token with `invalid_audience`, so each
  would cost another consent and another sign-in — for ground the hosted `workiq`
  preview already covers over Graph paths.

- **A plugin catalogue, so finding a plugin no longer means already knowing its
  package name.** **Settings → Plugins** now opens with an **Available** list —
  a curated directory of plugins with a one-click install — and the docs site
  publishes the same directory at
  [`/plugins`](https://precursor.vuptime.io/plugins), one page per plugin.

  The catalogue is **bundled with Precursor rather than fetched**: it works
  offline, adds no network failure states, phones nothing home, and every entry
  was reviewed in a pull request before it shipped. The trade — a newly listed
  plugin arrives with the next release — is the right one for a curated list.

  **One file is one plugin.** An entry is a single markdown page under
  `website/plugins/`, whose YAML frontmatter is the metadata and whose body is
  the documentation page, served both on the site and from the app's own offline
  `/docs`. Submitting a plugin is therefore adding one file and opening a pull
  request — see [Submitting a plugin](https://precursor.vuptime.io/plugins/submitting).

  An entry may only ever supply a **bare PyPI project name**: anything
  expressing a location — a URL, a path, an `@` requirement, an extra, a version
  specifier — is refused when the catalogue loads, and again in CI. Without that
  the catalogue would make a merged pull request into code execution on every
  machine that opened the panel. Installing from it calls the same gated
  endpoint as typing a name by hand, so the existing three gates (loopback bind,
  a request addressed to it, explicit opt-in) are unchanged; the catalogue is a
  shortcut to a name, not a second way in.
- **The menu-bar icon says whether the app is *ready*, not just whether it is
  up.** Starting, stopping and — above all — *updating* used to render exactly
  like "running": a fully-coloured icon claiming the app was there and clickable
  while its own code was being replaced underneath. Those states now draw the
  mark in grey with an ellipsis in the speech bubble, the universal "working on
  it". Grey alone would have read as "stopped", so the glyph carries the
  difference.
- **The tray menu leads with where the install stands**, on a line above the
  actions: 🟢 *Up to date*, 🟡 *Update available — 2026.9.1*, 🔴 *Could not
  check for updates*, ⚪ *Checking…*. The action entry can only describe the next
  click; this answers the question you opened the menu for. "Couldn't check" and
  "up to date" are different facts, and conflating them is how an install goes
  quietly stale.
- **A new "Open log file" entry in the tray menu**, opening the instance log the
  supervisor actually recorded (so it follows an instance started against a
  different data directory) and falling back to the logs folder when there isn't
  one yet. Like the data folder, it is deliberately *not* gated on a running
  instance: the log is exactly what you want when it won't start.
- **The tray now speaks up when a background check finds a new build**, instead
  of waiting for you to click the icon — with an **Update and restart** button
  where the desktop can draw one (macOS `osascript`, Linux `notify-send
  --action`). Anything other than an explicit yes leaves the build waiting in the
  menu, and each build is announced once, so a poll every half hour doesn't
  become an interruption every half hour. `PRECURSOR_UPDATE_NOTIFY` turns it down
  to a plain toast (`notify`) or off.
- **`precursor` now ships a `py.typed` marker (PEP 561), so the plugin API is a
  typed contract outside this repository.** Without it, mypy treats every
  `precursor.*` import in an *installed* environment as untyped and silently
  degrades the whole surface — `PluginRegistry`, the read models, the GitHub
  guards — to `Any`. Under `strict` that is the worst outcome available: a
  plugin author gets no errors *and* no checking of the one boundary most likely
  to break when Precursor moves.

  In-repo plugins never noticed, because they type-check against this source
  tree rather than a wheel. Extracting `precursor-kanban` to
  [its own repository](https://github.com/lrivallain/precursor-kanban) is what
  surfaced it.
- **The MCP tool catalogue survives a restart.** Each server's tool list is
  stored and restored at startup, so **Settings → MCP** renders and the model is
  offered tools before anything has connected. The live session stays
  authoritative: a call that finds a tool the server no longer exposes re-lists
  the catalogue and retries once instead of reporting a tool that isn't there.
- **Enabled MCP servers are warmed in the background at startup**, one at a time,
  so the first prompt of a session no longer pays connect + initialize +
  list_tools for all of them at once (and an `npx` spin-up for the stdio ones).
  It never blocks startup or a request, and it never starts an interactive
  sign-in — a server whose credential has lapsed is skipped, so launching the app
  can't pop a browser window at you. Tunable via `PRECURSOR_MCP_WARMUP_ENABLED`,
  `PRECURSOR_MCP_WARMUP_DELAY_SECONDS` and `PRECURSOR_MCP_WARMUP_GAP_SECONDS`;
  each server that resolves publishes an `mcp.server_state` event.

### Changed

- **Installation is one prescriptive path instead of a menu of three.** The guide
  used to open with "Option A / Option B / Option C" and expect a newcomer to
  weigh a background install against `uvx` against a source checkout before they
  had run the app once — with plugin extras, the retired `agents` extra and the
  Copilot CLI resolution order interleaved into the same page. For an opinionated
  tool that is the wrong shape: the first page should say what to do, not offer a
  decision.

  It now reads *install uv → run one command → open the URL*, with everything
  else demoted to an **Other ways to install** section that names the reason each
  alternative exists (try it out, no login item, Windows, stable channel,
  contributing). The Copilot CLI resolution order moved to
  [agents mode](https://precursor.vuptime.io/features/agents-mode#pointing-at-a-specific-cli),
  where it belongs, and the dev-stack launch options moved to the contribution
  guide. The quick start now starts from a *running* app rather than re-teaching
  how to launch one, and the README leads with the install rather than the stack.

- **The kanban board now ships from
  [its own repository](https://github.com/lrivallain/precursor-kanban).** It was
  the last in-repo plugin, and being in-repo was doing it no favours: it built
  with the host's Vite config, borrowed the host's TypeScript `paths` for
  `@precursor/host`, and — because Tailwind scans from the git root — had its
  utilities land in the host's stylesheet, so a *real* install would have
  rendered the board unstyled. Out of tree it builds and injects its own.

  Nothing changes for users: `precursor-ai[kanban]` still installs it, now
  resolved from PyPI (`precursor-kanban>=2026.9`) instead of a workspace member,
  and the board keeps its own release cadence rather than inheriting the host's
  CalVer. `precursor.plugin_api` and the `py.typed` marker are what make that
  work.

  For this repository it means no `plugins/` tree, no `uv` workspace, no
  `make plugins-build`, and a release that builds one distribution again.
  `tests/test_plugins.py` covers the plugin seam with a stub, so the suite no
  longer needs a plugin installed to test the host side of it.

- **One stale credential no longer stalls unrelated work.** The MCP sign-in gate
  ran *before* the model: if any enabled server was parked in `needs_auth`, the
  turn waited on that sign-in for up to five minutes before generating a single
  token. With three Entra credentials in play, an expired WorkIQ token held up
  questions that had nothing to do with WorkIQ.

  The turn now starts immediately and the sign-in is requested at the moment a
  tool actually needs it — naming the tool, which the old blanket prompt could
  not. The call then retries while it waits for that sign-in. This is not the
  tools being hidden: a blocked server still contributes its catalogue to what
  the model is offered, because dropping it is what makes a model answer from
  memory instead of calling the tool. A sign-in that never arrives yields an
  explicit tool error rather than a confident guess. Unattended runs (the
  scheduler) raise the banner but fail fast, since nobody is there to complete
  a browser flow.

### Fixed

- **Signing in to one WorkIQ server now counts for every server sharing that
  credential.** The Agent 365 endpoints authenticate with a single Entra token,
  but only the server you clicked was re-pointed at the fresh one — its siblings
  stayed parked in `needs_auth`, each showing an amber **Sign in** for a
  credential they already had. Clicking through them ran a full
  clear-and-regrant per server, so six built-ins meant six browser sign-ins for
  the two credentials Precursor actually holds.

  A completed sign-in now adopts the whole credential: every server on it is
  re-pointed at the new token, reconnected, and broadcast as resolved so other
  windows drop their banner too. **Settings → MCP** stops inviting the mistake as
  well — the sign-in button belongs to the *credential*, so the servers sharing
  one read `signs in with WorkIQ Teams` instead of offering a redundant prompt.

- **A custom MCP server could shadow the built-in `drawio`.** The list of names
  reserved for built-in servers was maintained by hand and had drifted from the
  catalogue, so adding your own server called `drawio` was accepted instead of
  rejected. The list is now derived from the built-in catalogue, which also
  closes the drift for every server added from here on.

- **A note filed from the MCP server now appears in the UI, and the tool says
  which topic it landed in.** Asking the assistant to append a note wrote it to
  the database and then showed you nothing: the browser kept rendering the topic
  as it was before, and the only confirmation was a tool result naming an
  internal `topic_id` — a number the app never displays — so there was no way to
  tell a silent failure from a note filed under the wrong topic.

  Two causes, both fixed. The built-in `precursor` server runs as a **stdio
  subprocess**, sharing the app's database but not its in-memory event bus, so
  the `message.changed` event announcing the write was published onto that
  child's own empty bus and reached no window. Children are now handed a
  loopback relay URL and a per-run token, and forward their events to the app
  process (`POST /api/events/publish`), which republishes them onto the real bus
  — so the write shows up live, in every window, including the one whose chat
  turn triggered the tool. The endpoint accepts only data-refresh event types
  and only with the token, so the worst it can ever do is make a window refetch
  data it already has.

  `append_note` and `post_message` now also return the destination as `topic`:
  its **title**, its slug **path**, and the in-app **URL** — pasteable straight
  into the browser — so "filed under `customers/sanofi/avm-alz`" replaces "filed
  under topic 42" and a mis-targeted note is obvious at a glance.

- **A remote MCP server that drops mid-turn no longer burns the rest of the
  turn.** When a hosted endpoint went away under a live session — a rolling
  deploy, a gateway blip, or the server forgetting the session id it issued —
  the MCP SDK reported it as the bare phrase `Session terminated`, which is
  emitted only on an **HTTP 404** from the remote. Because sessions are kept
  warm and reused, the dropped one stayed poisoned: every subsequent call failed
  identically until something happened to retire the worker.

  A dead session is now classified as a **transport** failure rather than a bad
  call — a remote 404, any 5xx, or a socket teardown recycles the session and
  **retries the call once**, which rides out a blip. A second failure stops
  there rather than hammering a downed endpoint.

  The wording mattered as much as the retry. Told only "session terminated", the
  model read it as its own fault and re-planned the call with smaller arguments,
  then advised re-authenticating — while the credential was valid the whole
  time, so a fresh token could not have helped. The tool error now names the
  server and the real status, states that neither the arguments nor the sign-in
  are at fault, and asks for the outage to be reported rather than an answer
  guessed.

- **A catalogue entry's install command is now shown where you're looking.**
  When the in-app installer is off, the entry's button was labelled "Show
  command" but only wrote the package name into the free-form box further down
  the panel — so the visible result was a text input quietly gaining a word,
  while the command itself stayed small, muted and elsewhere. The button is now
  "Install command", reveals the exact command for this environment on the card
  itself, and copies it to the clipboard in the same click.

- **The in-app installer's consent could be granted but never withdrawn.** The
  "Let Precursor install packages for me" checkbox lived inside the install box
  and was rendered *only while the permission was off*, so ticking it made it
  disappear — leaving no way to turn it back off short of editing the database.
  It now sits at the top of **Settings → Plugins** and stays visible, reflecting
  and toggling the setting in both directions. A permission you can't revoke
  isn't really a permission.
- **Edits to a Live meeting summary are no longer lost on reload.** The recap
  was persisted only when it was *generated* or *posted to a topic*, so the one
  thing the tab invites you to do — tune the draft by hand — was the one thing
  it didn't keep: reopen the session, or reload with no topic attached, and your
  wording was replaced by the last text the server had seen. The Summary tab now
  **autosaves like the Notes tab** (debounced while you type, with a "Saving… /
  Saved" marker), and also flushes when the session ends or you navigate away, so
  an unposted recap survives.
- **`PRECURSOR_EXTRAS=` now installs the lean core** instead of silently
  reinstating the default extras. `install.sh` read it with `${…:-default}`,
  which cannot tell "unset" from "deliberately empty", so the one way to ask for
  a Precursor without the board and the tray was the one thing it ignored — and
  had it not, the requirement would have been the invalid `precursor-ai[]`.
- **`precursor.log` is written again once Precursor runs as a login item.** The
  file only ever existed because `precursor service start` redirected the child's
  stdout into it — so the moment a launchd agent or a systemd unit took over the
  process (which is what `precursor service install` sets up, and what
  service+tray mode *is*), the service manager captured stdio somewhere of its
  own and the log froze at the last supervisor-started run. Nothing said so:
  `service status`, `precursor service logs` and the tray's **Open log file**
  went on naming a file that had stopped being written days earlier, while the
  real output piled up unrotated in `launchd.app.err.log` (15 MB in a week on the
  reported install). The app now configures its **own rotating file handler**, so
  `precursor.log` is the same file in every mode — terminal, supervised child,
  launchd, systemd — and is capped by `PRECURSOR_LOG_FILE_MAX_BYTES` /
  `PRECURSOR_LOG_FILE_BACKUPS` rather than growing forever. Where a service
  manager already captures stderr, the console handler is dropped so no line is
  written twice; a supervised child's raw pipe moves to `precursor.out.log`,
  which now holds only what logging can't catch (an import error, a pre-config
  traceback) and is trimmed at each start.
- **The menu-bar icon logs its own failures.** The tray process never configured
  logging at all, so every `logger.error` in it reached only `logging.lastResort`
  — which drops INFO entirely and writes the rest, unformatted, to a stderr its
  login item throws away. It now writes `tray.log` beside the instance log. Its
  own file, not the app's: "the icon failed" and "the server failed" are
  different questions, and two processes rotating one file race.
- **`precursor service start --foreground` no longer swallows its own startup
  messages.** It is what the login item runs, and it logged before anything had
  configured logging — including the INFO line explaining that it was exiting
  cleanly because another instance was already serving, which is precisely the
  message you go looking for when a login item appears to do nothing.
- **`precursor service install` no longer uninstalls the login item it was
  asked to re-install.** `launchctl bootout` signals the job and returns; it
  does not wait for the process to die. Precursor's shutdown is a graceful
  uvicorn one and takes several seconds, so bootstrapping the same label
  immediately afterwards landed while launchd still had the old job and failed
  with the opaque `Bootstrap failed: 5: Input/output error`. The old job was
  already booted out by then, so re-running `install` over a *running* login
  item left nothing registered at all — reproducible on every attempt, on both
  the app and the tray unit. Unloading now polls `launchctl print` until launchd
  has actually let go of the label (measured at ~5s for the app) before loading
  the new job. `stop_unit` and `uninstall` wait too: "stopped" has to mean the
  port is free, or a stop-then-start hands the new process one the old still
  owns.
- **An optional plugin your package index can't serve no longer blocks the whole
  self-update.** `precursor service update` reinstalls the tool with the extras
  it was installed with, so a single unresolvable one — `precursor-kanban`, on a
  restricted mirror that hasn't ingested it — made `uv` fail the resolution and
  left the host stranded on its old build. Before the plugin moved to its own
  repository the wheel travelled with the release and the index was never asked,
  so nothing showed until it did.

  The update now retries once without the extras that only pull a Precursor
  plugin (recognised from the distribution metadata, so it doesn't hardcode a
  list), and reports what it gave up: *"Installed 2026.9.0. Skipped kanban — not
  installable from your index: …"*. A failure that survives dropping them is
  raised as before, unchanged — degrading must not turn a real breakage into a
  fake success. Extras that pull libraries the host itself uses (`tray`,
  `postgres`) are never dropped.

- **A failed update says why.** The error led with the command — an install line
  carrying a full wheel URL — so the tray notification truncated it and left
  "updating failed" as the only signal, with `uv`'s actual explanation cut off.
  The reason now comes first, flattened out of `uv`'s box-drawing tree into one
  sentence, with the command last and URLs shortened to their filename.

- **`PRECURSOR_UPDATE_EXTRAS` can now drop an extra**, with a `-name` entry
  (`PRECURSOR_UPDATE_EXTRAS=-kanban`). The setting is unioned with `uv`'s install
  receipt so an update can't silently uninstall what you have, which also meant
  an extra recorded there could never be given up — reinstalling the tool by hand
  was the only way off it.

- **A WorkIQ token is now renewed when the keep-alive says so, not 30 seconds
  before it dies.** Two thresholds decided "renew this" and they disagreed. The
  keep-alive opened a session once a token was within five minutes of expiring;
  the MCP SDK gates its only refresh branch on its own freshness check, which
  called the same token valid until one minute out. In between, the keep-alive
  spent an MCP session per tick doing nothing, read back the unchanged token and
  logged `keep-alive: token renewed` — four wasted sessions a cycle and a trace
  that asserted a renewal that never happened. Renewal only really occurred with
  about 30 seconds of headroom: one delayed tick from the expired credential and
  browser prompt this machinery exists to prevent.

  The deliberate-renewal path now states its intent, so the refresh it asks for
  is the refresh it gets. That is confined to it on purpose — a token marked
  spent whose refresh then fails transiently is sent with no auth header at all,
  and the 401 escalates to a full sign-in, so chat turns and the warm pool keep
  the SDK's cautious default.

  How far ahead of expiry that happens is no longer a setting
  (`PRECURSOR_WORKIQ_KEEPALIVE_REFRESH_MARGIN_SECONDS` is retired; a stale value
  in your environment is ignored). It is derived from the token's own lifetime —
  a quarter of it, never less than five minutes — because the only question that
  matters is how many attempts fit before the token dies. Real tokens get some
  17-22 minutes of runway and a dozen-plus retries instead of one.
  `GET /api/mcp/auth/diagnostics` reports the resulting `renewal_lead_seconds`
  per credential, and a refresh that renews nothing is now recorded as the
  anomaly it is rather than as a success.

- **A built-in plugin now updates with the host.** `precursor-kanban` pinned a
  static `version = "0.1.0"`, so every build produced a byte-identical wheel
  name and the nightly manifest advertised an unchanging URL. uv saw the
  requirement already satisfied, skipped the download, and left a *current* host
  paired with a plugin frozen at whatever build landed first — an upgrade that
  visibly did nothing, which is the exact pairing `RELEASING.md` promises never
  happens.

  Built-in plugins now inherit the host's CalVer from the same git tags, so a
  plugin wheel carries the commit it was built from. `root = "../.."` is the
  only line tying one to this repository, so extracting a plugin to its own
  repository and release cadence stays a one-line change.

  Two consequences of the same bug are fixed with it: the release workflow ran a
  bare `uv build`, so tagged releases never built the plugin at all and
  `precursor-ai[kanban]` was unresolvable from PyPI; and a static version made
  the plugin unpublishable anyway, since PyPI refuses to re-upload one. Releases
  now build and publish every workspace package, and verify each wheel against
  the tag.

  Publishing needs a matching PyPI trusted publisher per distribution — see
  `RELEASING.md`.

- **A compaction test no longer fails on the residue of the suite that ran
  before it.** `test_compact_reports_a_size_delta` asserted that `VACUUM` never
  grows the file — but against the *shared* scratch database every test writes
  to, so the assertion was evaluated on whatever a thousand preceding tests
  happened to leave behind. `VACUUM` is under no obligation to shrink a file
  whose free-page layout is already compact, so it tripped in roughly half of
  full-suite runs while passing every time the file was run alone.

  The behaviour is worth guarding — a `VACUUM` in WAL mode once *grew* the
  database, because the WAL was checkpointed on only one side of the rebuild —
  so the assertion is not relaxed, it is moved somewhere it means something.
  Compaction is now measured on a database the test owns: a WAL-mode temporary
  file filled with ~8 MB and then mostly, deliberately not entirely, emptied.
  Keeping rows alive is what makes the check bite, since VACUUM rewrites the
  surviving database through the WAL — restore the old bug and this fixture goes
  from 8.3 MB to 11.6 MB, where a fully emptied one would have shrunk anyway and
  said nothing. Owning the churn also makes the assertion strict: compaction has
  to actually reclaim, and the WAL has to be empty afterwards. The endpoint keeps
  its own test for the shape of what it returns.

### Changed

- **The kanban board no longer needs a configured GitHub repository.** It was
  never really used: the repo is only ever read for its *owner* —
  `list_repo_projects` discards the name — so it was one way of spelling "list
  this account's boards", equivalent to adding that account as a source. Worse,
  `GET /projects/{id}/board` and the card-move endpoint demanded a repo and then
  **discarded it**, because project and item ids are global GitHub node ids.
  A perfectly workable setup — a token plus an explicitly added project — was
  blocked on configuration it would never consult.

  A repository is now an optional default that contributes its owner's boards.
  What the board actually requires is a token with the `project` scope, and the
  **issue associations** switch, which remains the master control for the GitHub
  surface.

- **Kanban has no Settings page.** Which boards you track is managed on the
  board: **+** adds one, right-click removes it. A second surface in Settings was
  the same list, one navigation further away.

  Two things only that page could reach are now in the picker itself, so nothing
  you configure can be invisible *and* unremovable: a collapsible **Hidden (N)**
  group (right-click → *Show on board*), and a **Not resolving** group listing
  sources that currently produce no board — renamed, revoked, made private, or
  simply empty — which previously vanished without trace.

  `GET /api/github/projects` returns `{projects, unresolved}` accordingly, and
  hidden boards are returned flagged rather than dropped.

- **A plugin's section is always visible once enabled.** Sections could gate
  themselves on app state and hide until it was satisfied — the kanban board
  disappeared entirely without a configured GitHub repository. That optimised
  for the wrong thing: an installed, enabled plugin that is nowhere in the
  sidebar is indistinguishable from a broken one, which is why Settings →
  Plugins needed a warning strip ("**Kanban** is hidden: No GitHub repository is
  configured") to explain the absence.

  Enabled now simply means visible. Kanban appears with or without a repository
  and explains the setup step in the board itself, where the user actually
  lands, instead of the developer-facing guard error the API returns. The
  warning strip in Settings → Plugins is gone with the mechanism it described.

  **Breaking for plugin authors** (`HOST_API_VERSION` 1 → 2): `SectionPlugin`
  drops `unavailable`, and `@precursor/host` no longer exports
  `sectionUnavailableReason` or the `SectionEnabledContext` type. A section that
  needs setup should say so from `Main`. The backend's `PLUGIN_API_VERSION` is
  unchanged.

### Added

- **The kanban board manages its own projects.** Tracking a board used to mean a
  trip to Settings → Plugins → Kanban, and *untracking* one meant knowing that
  the picker's rows and the settings list are not the same thing. Both now happen
  where the boards are.

  The **+** next to the Precursor logo — core's own header button, which every
  section shares — opens the board's **Add a project** dialog instead of routing
  to a settings tab. **Right-clicking a board** in the picker offers to open it
  on GitHub, **hide** it, or **stop tracking** the source that added it, matching
  how the navigation panel behaves everywhere else.

  Those two removals are deliberately different, because what the picker shows
  and what settings store never lined up one-to-one. A *source* adds boards and
  can name a whole account, so removing one can take several boards with it —
  the menu says how many and asks first. Hiding always means exactly one board,
  which is what finally makes the projects owned by your **configured
  repository's** account removable: no settings entry produced them, so until now
  nothing could take them out of the picker. Boards therefore report where they
  came from (`source`, `source_ref` on `GET /api/github/projects`), and a new
  `hidden_projects` list is applied last, to the merged listing.

  Settings → Plugins → Kanban keeps the full picture and gains a **Hidden
  projects** list to undo a hide. It stays the place a *broken* source gets
  fixed: one that has been renamed, revoked or made private resolves to no boards
  at all, so it has no row in the picker to right-click.

  For plugin authors, `@precursor/host` now also exports `ContextMenu` and
  `useConfirm`, so a section's list rows behave like core's rather than growing a
  near-identical menu of their own.

- **Agents mode can be turned on from Precursor.** It was the only capability
  that could not be: everything else — a model provider, an MCP server, a
  plugin, backup, retention — is a switch in Settings, while Agents mode was a
  package extra. Enabling it meant leaving the app, knowing how you had
  installed, and running the right variant of a command that a later
  `service update` could silently undo.

  The Copilot SDK is now a **normal dependency**, so there is no Python package
  left to install by hand. What remains is the native Copilot CLI it drives, and
  **Settings → Agents** installs that in one click: it asks first (it is ~90 MB),
  runs as a background job so a slow download doesn't hang a request, and reports
  the SDK's *actual* error rather than a generic "unavailable". On success the
  runtime starts in place; only when that fails does the panel ask for a restart,
  and it offers one — handing off to the supervisor exactly as
  `precursor service update` does, or telling you it can't when the instance
  isn't supervised.

  This is safe to ship by default only because of the wheel's shape. Up to 1.0.2
  the SDK published six platform-specific wheels that each *bundled* the CLI
  (~145 MB unpacked); 1.0.4 replaced them with a single ~0.5 MB pure-Python wheel
  that downloads it on demand. The dependency is floored at `>=1.0.4` for that
  reason and nothing else, with a test guarding it, because the difference is
  invisible in a diff. The payload itself stays opt-in — just one click inside
  the app instead of a command outside it.

  The `agents` extra is retired but still declared (and empty), so installs that
  still name it — including any rebuilt from uv's install receipt — keep
  resolving.

### Changed

- **Settings → Agents no longer renders controls that cannot work.** Default
  model, approval policy, custom system message, watchdog, permission grants and
  blueprints were drawn whether or not a runtime existed — and several of them
  whether or not Agents mode was even switched on — so the panel read as
  "configured and ready" while the feature was inert, with the one honest signal
  buried among a dozen live-looking controls. They now require both: the feature
  on, and a runtime behind it.

  Switched off, the panel is just the toggle and timeline retention. In
  particular it no longer warns that the runtime "didn't start in this process"
  when the runtime was stopped *because* you turned Agents mode off — an alarm
  for a state the user chose.

  Nothing is deleted: the stored values are untouched and reappear exactly as
  they were.

  **Timeline retention** follows a narrower rule than "always visible". Its sweep
  is gated on `scheduler_enabled`, not on Agents mode, so it keeps pruning
  archived events after the feature is switched off — hiding the levers while
  that runs would leave no way to stop a background job quietly erasing history
  you may want to keep. So they stay reachable when events exist, and say so;
  with nothing on disk there is nothing to protect and the section goes away with
  the rest.

- **Settings → Workflows says that workflows need Agents mode.** Every workflow
  step runs an agent, so with Agents off these defaults configure a pipeline that
  cannot run — and the panel gave no hint of it. They stay editable (they are
  plain preferences, worth setting ahead of time, and they survive the toggle);
  they just no longer imply a workflow could start right now. Same wording the
  Workflows section already uses for its own empty state.

### Fixed

- **Every WorkIQ silent refresh was aimed at the wrong server, so none of them
  ever worked.** The symptom was a browser sign-in roughly every 80 minutes —
  the access-token lifetime — plus background tabs opening unbidden and turns
  stalling on a re-authenticate banner, against credentials that held a
  perfectly good refresh token throughout.

  The MCP SDK runs its refresh at the *top* of `async_auth_flow` but only
  discovers the authorization server later, in the branch that handles a 401. A
  freshly built provider — which every background renewal is — therefore reached
  `_refresh_token()` with no metadata, and the SDK fell back to deriving the
  token URL from the MCP endpoint. The grant was POSTed to
  `https://workiq.svc.cloud.microsoft/token` (400 `"Invalid request, no valid
  route."`) and `https://agent365.svc.cloud.microsoft/token` (404) — the
  *resource* hosts, which have no token endpoint. The SDK read those as Entra
  refusing the refresh, cleared the stored tokens and escalated to a full
  browser grant.

  Restoring each token's real expiry (previous release) made this visible rather
  than causing it: before, `is_token_valid()` always returned `True`, so the
  broken refresh branch was skipped entirely and the failure hid behind the
  401 path. Once expiry was honoured, every renewal entered a branch that could
  only fail — and discarded a renewable credential on the way out.

  Precursor now resolves the authorization-server metadata before the flow runs,
  so the refresh reaches
  `https://login.microsoftonline.com/organizations/oauth2/v2.0/token`. Only the
  URL changes: the metadata seeded is deliberately limited to the authorization
  server, because seeding the protected-resource document too would add an
  RFC 8707 `resource` field to the request body. Discovery is resolved once per
  endpoint per process, and a discovery outage degrades to exactly the previous
  behaviour rather than making things worse.

- **Installing failed outright when something else already held port 8000**, and
  said almost nothing useful about it: `error: The Precursor login item did not
  come up on port 8000`. That is the *first* thing a new user runs, and 8000 is
  a popular port — the login item was registered against a port it could never
  bind, so launchd/systemd dutifully retried a doomed start forever while the
  installer reported only the symptom.

  `precursor service install` now settles the port **before** registering the
  unit: a busy default moves to the next free port and is written to `.env` in
  the data directory, so the login item — which launchd and systemd start with
  no arguments — reads the port that actually works, and keeps reading it across
  reboots instead of drifting. Both facts are printed. A port you chose yourself
  (`PRECURSOR_PORT`, or the new `precursor service install --port`) is never
  moved silently; the install stops and says the port is taken.

  The two start paths change accordingly. The login item — unattended, and the
  one thing `KeepAlive`/`Restart` will retry forever — no longer exits non-zero
  when its port is taken at login: it bumps, logs where it went, and publishes
  the real URL in `runtime.json`, which is what `service status` and the tray
  read anyway. `precursor service start` does the same for a merely *defaulted*
  port, while a port you pinned still fails loudly there, and the "did not come
  up" error now names the port and says who is holding it.

- **The tray kept offering an update the app had already installed**, because it
  was comparing the published build against *its own* version. `__version__` is
  resolved once, at import, so a long-lived icon measures against the release it
  was **started** with, forever — no matter what the app is running. The result
  was a menu entry saying *"Update to dev245"* on a machine that had been
  running dev245 for over an hour, where the CLI (a fresh process) correctly
  reported nothing to do.

  The tray now compares its own version against the one the running instance
  recorded when it launched — a fresh process, so a disagreement means the icon
  is the stale one. It says so, offering **"Restart the icon (running an older
  build)"** in place of the update, and that entry bounces the tray's login item
  and drops the cached check. Detection rides the existing 3-second supervisor
  poll: no extra network call, no new state.

  This also covers updates that never go through `precursor service update` — a
  manual `uv tool install --force`, for instance — which restart nothing and
  previously left the icon lying until it was killed by hand.

- **Running the test suite killed the developer's own Precursor.** Every other
  external the tests touch is redirected — the database, the data dir, the skills
  dir — but login items are not addressed by an env var: launchd reads
  `~/Library/LaunchAgents` and systemd `~/.config/systemd/user`, both global to
  the user account. `supervisor.stop()` and `restart()` ask `managed_unit()`
  whether a *controllable* login item owns the instance, and it answered from the
  developer's real plist, so a supervisor test with a perfectly isolated data dir
  still ran `launchctl bootout` against the machine's actual running app.

  Booted out is worse than killed: `KeepAlive` can only restart a job that is
  still loaded, so the app stayed down until someone bootstrapped it by hand —
  with the tray left running against a server that no longer existed. `conftest`
  now redirects the single path chokepoint every platform goes through, and a
  test fails if that isolation is ever dropped.

- **Agents mode reported its runtime as missing on installs that had a perfectly
  good Copilot CLI.** `github-copilot-sdk` 1.0.4 replaced its platform-specific
  wheels — which *bundled* the ~90 MB native CLI — with a small pure-Python wheel
  that downloads the binary on first use. The private helper Precursor called to
  locate that bundled binary went with it, and because the whole lookup sat
  inside a bare `except Exception`, the failure was silent: **Settings → Agents**
  simply said *"Copilot CLI runtime binary not found for this platform"* on a
  machine where the SDK was installed, working, and a system-wide `copilot` was
  already on `PATH`.

  Resolution is now layered and no longer hinges on any single SDK internal:
  `COPILOT_CLI_PATH`, then the SDK's download cache, then a `copilot` executable
  on `PATH`, then the old bundled location for pre-1.0.4 SDK lines. Each step
  that reaches into the SDK is guarded independently, so the env var and `PATH`
  keep working even if those internals move again. The probe stays **read-only**
  — it reports what already exists and never triggers the SDK's download, because
  it runs on every Settings render and pulling ~90 MB as a side effect of drawing
  a toggle would be indefensible.

  Whatever the probe resolves is now handed to the SDK when the runtime starts.
  Left to itself the SDK ignores `PATH` and downloads instead, so a machine whose
  only CLI is a Homebrew install would have passed the probe and then pulled the
  binary anyway — and could have ended up driving a different one than Settings
  reported. `github-copilot-sdk` is also capped at `<2` now, for the same reason
  `mcp` is: Precursor reaches into its private CLI-resolution helpers, and an
  uncapped floor is what let a *patch* release break this in the first place.

- **Updating left the menu-bar icon running the previous release.**
  `precursor service update` restarts the app so the new code is serving, but
  the tray is a separate long-lived process: replacing the wheel changes the
  code on disk while the running icon keeps executing what it imported at
  startup. That is not cosmetic — the tray's menu is what drives the
  supervisor, so a stale icon kept offering the *previous* version's behaviour
  indefinitely — including, right after the release that fixed the restart
  race, the icon's own Restart entry still having the racing version of it.
  Both `service update`
  and the tray's own "Update and restart" now bounce the tray unit; the tray
  restarts itself last, so the service manager brings a fresh icon straight
  back. A tray started by hand is left alone, and a failure to restart it is
  reported as a note rather than failing an otherwise-successful update.

- **Updating a login-item instance left it dead with the port still taken.**
  Once registered, a launchd agent (or systemd unit) *is* the supervisor:
  `KeepAlive` and `Restart=` exist precisely to undo a kill. `precursor service
  restart` signalled the process anyway, so the manager immediately started a
  replacement while the supervisor started its own, and the two raced for the
  port. One won; the loser retried on a 30-second throttle forever. The
  symptom, from the tray's "Update and restart": the old instance is not
  replaced, the new one cannot bind, and the log fills with `Port 9000 is in
  use`.

  `start`, `stop` and `restart` now delegate to the service manager when a
  controllable unit owns the instance — `launchctl bootstrap` / `bootout` /
  `kickstart -k`, or the systemd equivalents. `kickstart -k` matters
  specifically: it kills and restarts in one operation, leaving no window for
  anything else to claim the port, which a stop-then-start cannot promise. A
  Windows Startup entry is only a shortcut executed at login, so it is not
  treated as a manager and the direct path still applies. An explicit
  `--port`/`--host` override also keeps the direct path, since the unit carries
  its own configuration.

- **A failed start could delete a healthy instance's state file.** `runtime.json`
  was cleared unconditionally on shutdown, so a process that lost the race for
  the port erased the record belonging to the instance that won it — leaving
  `precursor service status` reporting "not running" while the app was plainly
  serving. It is now cleared only by the process that owns it.

- **`precursor service update` silently uninstalled extras.** The reinstall was
  built from `PRECURSOR_UPDATE_EXTRAS`, a setting defaulting to `kanban` that
  the user had to remember to mirror by hand. So an install made with
  `precursor-ai[tray,agents]` came back as `precursor-ai[kanban]` on the next
  update, removing the menu-bar icon and Agents mode without saying anything.
  The extras are now read from uv's own install receipt — what is actually
  installed, rather than a copy that drifts — with the setting kept as the way
  to *add* an extra the current install doesn't have yet.

- **Restarting a login item that launchd had unloaded failed outright.** A plist
  on disk is not the same as a job launchd knows about, and the two diverge
  whenever the instance was stopped or its executable was replaced underneath
  it. `launchctl kickstart` needs a loaded job, so `service restart` reported
  *"Could not find service … in domain for user"* instead of starting it. Both
  start and restart now fall back to bootstrapping the job.

### Added

- **Precursor can now run as a background app instead of a terminal process.**
  Keeping it available all day meant keeping a terminal open, remembering which
  directory to be in, and repeating a multi-command ritual after every reboot —
  in one reported case `git pull && gh auth switch && make plugins-build &&
  uv run precursor --strict-port`, by hand, on every machine, at every startup.
  Each part of that is now unnecessary.

  A new `precursor service` command supervises a detached instance —
  `start`, `stop`, `restart`, `status`, `logs` — and `install` registers it to
  run at login (a launchd agent on macOS, a systemd *user* unit on Linux, a
  Startup entry on Windows), together with a second, independent unit for the
  menu-bar icon so both come back after a reboot. The tray unit is skipped where
  the `tray` extra isn't installed — a headless box gets no login item rather
  than one that fails every boot — and `--no-tray` opts out explicitly. It
  records what it started in `runtime.json` in the
  data directory, so there is one source of truth for "is it up, and where"
  rather than a port to guess: `status` exits non-zero when nothing is running,
  heals a state file left behind by a crash, and starting is idempotent so a
  login item, a tray click and a manual start can't produce two instances
  fighting over one database.

  `precursor tray` puts that on a menu bar (behind a new `tray` extra), showing
  Precursor's own mark in brand colour when the instance is running and grey
  when it is stopped, with entries to open the app, reveal the data folder,
  start, stop, restart, and update. It is a convenience only — every action has
  a command behind it (the data folder is `precursor service data-dir
  [--reveal]`), so the app never depends on a GUI being present. The data-folder
  entry stays enabled while the instance is stopped, because the database and
  the logs are exactly what you need when it won't start.

  `precursor service update` replaces the manual `git pull`. It detects how
  Precursor was installed and does the right thing for each shape: a checkout is
  updated with `git pull --ff-only` plus a plugin-frontend rebuild, an installed
  wheel by reinstalling. `GET /api/version/check` exposes the same check to the
  UI; *applying* an update deliberately stays out of the API, since it replaces
  the very process serving the request.

  Feeding all of this, a new **nightly channel** publishes a rolling build of
  `main` as a prerelease on every push, wheels and all. That removes the reason
  to run from a source checkout at all: the published wheel already carries the
  SPA, the in-app docs and every plugin frontend, so tracking `main` no longer
  needs a clone, Node.js, or `make plugins-build`. A single command now goes
  from nothing to installed-and-running-at-login:

  ```bash
  curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
  ```

  Development is untouched: a source checkout keeps its defaults, so every
  worktree still runs `precursor --dev` on an auto-bumped port against its own
  database, and cannot collide with the installed instance.

- **`PRECURSOR_GITHUB_CLI_USER` pins which `gh` account supplies the token.**
  `gh auth token` follows the CLI's *active* account, so with several logins the
  credentials Precursor resolved depended on whoever last ran `gh auth switch`
  in an unrelated shell — fine interactively, and unworkable for an instance
  started at login. Naming the login passes `--user`, making the resolution
  deterministic. A token saved in **Settings → GitHub** still wins, as before.


- **An agent can now be told which MCP servers it may see.** Agents attached
  *every* enabled server, and only workflow steps could narrow that — so a
  focused conversational agent got the browser, the CRM and the shell whatever
  its instructions said, and paid for all their schemas on every turn. The
  settings drawer gains an **MCP servers** row, the same tri-state allowlist the
  step editor already had: **All** (default, unchanged behaviour), a chosen few,
  or none at all. It is a real allowlist — an unpicked server is never attached,
  so the agent cannot call it, which prompt instructions alone never achieve.

  The list stays dynamic: it is whatever is registered and enabled right now, so
  a server installed later is offered without editing anything and an unscoped
  agent picks it up on its next turn. Unknown names are kept rather than dropped
  (struck through, red when nothing by that name is installed, amber when it is
  installed but switched off), so a scoped agent survives a move between
  machines. A workflow step's own scope still wins for the duration of that step,
  so narrowing a shared agent can't silently disarm a pipeline.

- **A plugin whose interface is missing now says so.** A plugin's frontend is a
  build product inside its package, so a source checkout that hasn't built it
  gets the backend without the UI: the section is advertised, the SPA has nothing
  to import, and it simply never appears while Settings → Plugins reports the
  plugin installed, enabled and healthy. That state is now detected and
  explained, and `make sync`, `make dev` and `make backend` build in-repo plugin
  frontends so it stops arising during development at all.

- **Plugins can bring their own settings page, and the Kanban board can track
  projects that aren't yours.** A plugin declares
  `registry.add_settings_page(...)` and registers a React panel; it appears in
  the Settings modal under a new **Plugins** group. Its values are one opaque
  JSON document per plugin, stored under `plugin.<id>` and served at
  `/api/plugins/installed/<id>/settings` — core never looks inside, so a plugin
  can add, rename and drop its own keys without touching core's settings schema,
  and two plugins can't collide. The plugin's backend reads the same document
  (including from its MCP subprocess), so the panel and the tools agree.

  The board is the first thing to use it. It has always listed the projects owned
  by whoever owns the repo in Settings → GitHub, which is a fine default and a
  poor ceiling: the board you care about is often somebody else's. **Settings →
  Plugins → Kanban** now takes extra sources — an account (`acme-corp`, every
  open project it owns) or a single project (`acme-corp#4`, or its GitHub URL).
  Extras are additive and de-duplicated by project, and once boards come from
  more than one account the picker labels each with its owner, which is what
  tells two projects called "Roadmap" apart. A source you have lost access to is
  skipped rather than taking the whole picker down. The board's sidebar header
  gained a **+** for it, in the section's own tint: a plugin section can now
  supply the header's "New …" action (`onNew`) and reach its own settings page
  (`SectionHost.openSettings`), so core keeps owning where the button sits while
  the section decides what it means.

- **Plugins are now a real extension system, and the Kanban board is the first
  one.** A plugin is one Python package that can bring three things at once — a
  whole **UI section**, its own **MCP tools**, and **API routes** — and
  installing it turns all three on. The GitHub Projects v2 board left core to
  become `precursor-kanban`, which now carries its own routes, schemas, tests,
  a `kanban.board` MCP server and its own compiled frontend.

  The hard part was UI. The SPA is pre-built and served by FastAPI, so a plugin
  previously had to be inside that build to render anything. Now a plugin ships
  a built ES module **inside its wheel**; Precursor serves it from the installed
  package and the SPA imports it at boot. The load-bearing detail is that there
  must be exactly one React on the page — a second copy makes every hook a
  plugin calls throw — so plugin bundles leave `react`, `react-dom`,
  `react/jsx-runtime` and `@precursor/host` external, and an injected **import
  map** points all of them at the host's own runtime module. That module is also
  the plugin SDK: the section registry, the HTTP client, shared components. A
  plugin installed from PyPI now contributes a section with no build step on the
  user's side at all.

  Tools were the cheap half: `registry.add_mcp_server(module=…)` launches a
  plugin's server the same way core launches its in-tree ones, and it joins the
  same catalogue, toggles and probing.

  Around that: `precursor.plugin_api` is a curated, versioned import surface so
  plugins never reach into `precursor.backend.*`; contributions are attributed
  to the registering plugin automatically; a plugin that fails to load is
  recorded rather than silently missing. **Settings → Plugins** lists what's
  installed with its version, homepage and exactly what it contributes, and can
  install, enable, disable, uninstall and restart. Toggling applies live: the
  sidebar, home launcher, palette and router all re-derive while the panel stays
  open, and disabling is total — descriptors vanish, routes answer 404, MCP
  servers leave the catalogue. A section can also gate itself on app state (the
  board needs a GitHub repo), and the panel says so — "Kanban is hidden: no
  GitHub repository is configured" — because an enabled plugin that appears
  nowhere is otherwise indistinguishable from a broken one. Installing needs a restart (entry points resolve once at startup,
  so a live import would leave a half-installed plugin), and the panel offers
  the button; the installer runs out-of-process.

  Installing runs a package's own code as whoever runs Precursor, and Precursor
  has no authentication, so the mutating endpoints are gated three ways: the app
  must be loopback-bound, the request must *address* that bind (the `Host` header
  is checked and a cross-site `Origin` refused — a bind address is no defence
  against DNS rebinding, which is same-origin to the browser and triggers no
  preflight), and the user must switch the installer on. It is off by default;
  listing and toggling plugins are not gated, and the exact command to run by
  hand is always shown.

  Nothing in core names the board any more: the sidebar, home launcher, command
  palette, router and section palette all iterate whatever is installed.

  Two host bugs surfaced while doing it for real: plugin routers were mounted in
  the FastAPI lifespan — *after* the SPA catch-all — so every plugin endpoint
  silently served `index.html`; and `discover()` re-ran each plugin's `register`
  per app instance, duplicating its routers and its `/api/plugins` descriptors.
  Discovery now happens while the app is being built, once per process.

- **Settings → Usage stats gained a storage cleanup cockpit.** It lists every
  retention sweep with what it would remove **right now** under your current
  settings — a dry run, nothing is deleted to produce the figures — and lets you
  run any of them on demand instead of waiting for the daily ticker. A
  **Compact database** action then returns the freed pages to the filesystem:
  Precursor runs SQLite with `auto_vacuum` off, so deleting rows alone only
  marks space reusable and never shrinks the file, which is why a cleanup could
  previously look like it had done nothing. New endpoints back it:
  `GET /api/stats/cleanup`, `POST /api/stats/cleanup/{key}` and
  `POST /api/stats/compact`. See [Storage & retention](https://lrivallain.github.io/precursor/features/storage).

- **Agent timelines are now bounded.** The archived event trace behind the
  Agents timeline (`agent_events`) had no retention at all and became the
  largest object in a busy install. Two independent levers now govern it —
  `agent_event_retention_days` (default 30) prunes by age, and
  `agent_event_max_per_session` (default 2000) caps how many events any one
  agent keeps, newest first. Both are in **Settings → Agents → Timeline
  retention** — next to the feature they govern, the same way Live owns its
  transcript window — and either can be disabled with `0`. Agent traffic is bursty
  rather than aged, so the per-session cap is what actually bounds a single long
  autonomous run; the window alone wouldn't reach it for weeks. An agent keeps
  its result, artifacts, state and posted messages either way, and a **running**
  agent is never pruned — its live timeline is rebuilt from those rows after a
  restart.

- **The agent dashboard gained a name filter.** A search box in the fleet header
  narrows the board to agents whose title matches what you type, stacking on top
  of the KPI tile filters (so you can search *within* "Needs you"). The
  "filtered to…" chip names both the active lane and the search term, and clears
  them together.

- **A topic's collection is now part of its URL, and every topic has a
  permalink.** The readable address gained the collection slug —
  `/topics/client-a/csu/capacity-review` — so a bookmark or a shared link lands
  in the right collection instead of whichever one the browser last remembered,
  and `/topics/<collection-slug>` on its own opens that collection's start
  surface. Because that address moves whenever a topic is renamed, re-parented
  or moved between collections, every topic also gained an immutable
  `/t/<uuid>` **permalink**: opening it resolves the topic and rewrites the
  address bar to the readable form. Copy it from **topic settings →
  Permalink**. Links minted before collections joined the URL still resolve —
  the trailing slug is unique on its own. Topics over MCP report the same
  collection-prefixed `path`, and a new `public_id` field.

- **A WorkIQ sign-in prompt can now be explained after the fact.** Every decision
  taken about a WorkIQ / Agent 365 credential — the keep-alive's verdict, the
  Entra `AADSTS…` code that refused a silent refresh, each leg of a re-auth,
  whether a token was even renewable — reports to a dedicated `precursor.mcp.auth`
  channel prefixed `[workiq-auth]`, tagged with an **episode** id that stitches a
  keep-alive failure, a hands-free pass and a manual click into one story. The
  reason a renewal was refused was previously *nowhere*: the MCP SDK logs a bare
  `Token refresh failed: 400`, naming neither the credential nor Entra's answer,
  so the single most useful datum for choosing a renewal strategy was discarded
  before anyone could see it. The channel has its own level
  (`PRECURSOR_WORKIQ_AUTH_LOG_LEVEL`, default `debug`) independent of the app
  log level, because a lapse is rare, only reproducible in the wild, and useless
  to diagnose after the fact — the trace has to already be running when it
  happens. It is silent outside an auth episode.

- **`GET /api/mcp/auth/diagnostics` — the whole episode in one request.**
  Terminals scroll and a packaged app has none, so the same records are kept in
  memory and served alongside the state that explains them: settings in force,
  then per credential whether a token is stored, whether it has a **refresh
  token** at all, when it expires, how long it's been idle, and whether connects
  are being fast-failed. `await precursorWorkiqAuthReport()` in the console
  merges it with the browser-side trace and puts the result on your clipboard,
  ready to paste into an issue. Episode records are buffered apart from the
  keep-alive's ambient heartbeat — otherwise a credential that lapsed overnight
  would have been pushed out by a thousand once-a-minute "nothing to do" ticks
  before anyone looked — and the ticker now reports a verdict only when it
  changes. Token values never leave the process; secrets and account names are
  reduced to `<present:N chars>`, and Agent 365's ~37-entry scope list is
  summarized to a count plus whether `offline_access` is in it.

- **Topics returned over MCP now carry a resolved `path`.** `get_topic`,
  `list_topics` and the topic hits from `search` include the topic's ancestor
  slugs joined root-first, prefixed by its collection slug
  (`client-a/csu/cto/capacity-uat-interim-pierre`) so the value mirrors the URL,
  and a caller no longer has to re-fetch every parent to rebuild it. A workflow
  step that matched meetings to topics was spending half its MCP calls — 7
  `get_topic`s against 7 real `search`es — climbing `parent_id` by hand, with a
  depth guard and a cycle guard in its prompt. Paths resolve from a single
  `(id, slug, parent_id)` index load, so a 200-topic response is still one extra
  query, not 200 chain walks, and a cyclic parent link terminates instead of
  hanging.

- **`append_note` — file text into a topic without paying for a turn.** The
  built-in `precursor` MCP server gains an `append_note(topic_id, text)` tool
  under a new **Append notes** capability toggle. It persists the text verbatim
  and returns, which is what a caller filing an already-written briefing, digest
  or summary actually wants; `post_message`, the only prior option, spends a
  full generation replying to it. Previously the only way to append a note over
  MCP was to POST to the app's own HTTP API through the generic `fetch` server —
  a round trip that made a workflow step hand-encode JSON, and in practice cost
  a multi-minute retry loop before the note landed.

- **A file the assistant touches in a workspace is one click away.** When a turn
  calls `drawio` or `workspace-fs` to read or write a file, the tool call in the
  transcript now carries an **Open** chip naming it — clicking it switches to the
  Files section with that file already open (and Back returns to the
  discussion), instead of leaving you to find it in the tree. Reads link too, so
  a diagram the assistant merely inspected — or a file whose read was truncated —
  is just as reachable. Both servers annotate the result with the workspace and
  path, which the backend lifts into the tool call's metadata, so the chip never
  costs a parse of the file a read returned; the route is rebuilt from the
  workspace + path rather than from a URL string an MCP server supplied. A
  failed call, a folder, or a path that can't be turned into a safe route
  carries no link.
- **`.drawio` files are now editable in the Workspace, with a self-hosted
  editor.** Opening a diagram in the Files section embeds the draw.io editor
  directly, with an **XML / Diagram** toggle to drop down to the raw mxGraph
  source. Edits flow into the same buffer as any other file, so the dirty
  marker, **Save** and `git diff` work unchanged — closing the loop on the
  `drawio` MCP server below: the assistant authors the diagram, you open it and
  adjust it in place.

  The editor is served from **this instance** (`/drawio/`) rather than
  `embed.diagrams.net`, and the frame runs with `offline=1&stealth=1`, so
  diagram content never reaches an external origin and editing works with no
  network at all. The webapp is **not** bundled in the wheel — the release is
  ~53 MB (~150 MB extracted) — so the first diagram you open offers a one-time
  install into `<data_dir>/drawio/<version>/`. `PRECURSOR_DRAWIO_VERSION` pins
  the release and `PRECURSOR_DRAWIO_DOWNLOAD_URL` points it at an internal
  mirror; superseded versions are pruned on upgrade.
- **New built-in `drawio` MCP server — diagrams as editable files.** The
  assistant can now author native `.drawio` documents (plain mxGraph XML)
  straight into a [workspace](https://lrivallain.github.io/precursor/features/workspaces)
  working tree, so a diagram is reviewable in a `git diff`, commit-able from the
  Workspace UI, and still editable in draw.io — unlike a rendered image. It
  shares `workspace-fs`'s sandbox (`safe_join`), so nothing outside
  `workspaces_dir/<slug>` is reachable.

  The server owns **layout**: the model describes a graph — nodes, edges and
  freely **nested `groups`** (region → VNet → subnet → resource) — and Precursor
  places everything in layers with a barycenter pass to cut edge crossings,
  sizing each container to fit its children and its own title. Siblings with no
  edges between them are packed along the flow direction and wrap rather than
  running off the page, and edges may join groups as well as nodes.

  It also ships a **catalogue of ~700 verified Azure icons**, searchable with
  `search_shapes` and generated from draw.io's own palette by
  `scripts/build_drawio_shapes.py`. This matters more than it sounds: draw.io's
  Azure library is made of SVG *images*, not `mxgraph.azure2.*` stencils, so a
  guessed stencil name renders as a blank rectangle and an "Azure architecture"
  comes out as a grid of featureless squares. `create_diagram` resolves shape
  names through the catalogue and **reports what it matched**, so an unresolved
  icon is reported rather than silently drawn as a box.

  Ships `create_diagram`, `search_shapes`, `list_shapes`, `write_diagram_xml`
  (raw-XML escape hatch, validated), `read_diagram` and `list_workspaces`. Any
  preset field also accepts a raw mxGraph style so the AWS/UML/BPMN catalogue
  stays reachable. Output is deterministic (no timestamps or random ids), so
  regenerating an unchanged diagram leaves an empty diff.
- **A failed turn can be retried from the prompt that failed.** When a provider
  rejects a turn — or the tool loop hits its round cap — the prompt bubble now
  carries a **Retry** button that replays *that* prompt, so there is no ambiguity
  about what gets re-sent. The stream endpoints accept a `retry_message_id`:
  instead of persisting a second copy of the prompt, the backend reuses the
  original user message (attachments included) and deletes the failed tail — the
  partial answer, its tool rows and the error notice — so retrying after
  switching model or fixing credentials leaves a clean transcript. Works in both
  topics and chats.
- **Skills can be referenced mid-prompt, and from a chat description.** A
  `/skill-name` token is no longer only recognised at the *start* of a message:
  written anywhere in the text it is now substituted **in place** with the
  skill's instructions, so "take the notes below, `/rewrite` them, then
  summarise" reads as one instruction. The same substitution runs on the backend
  for a **chat description** — in both *context* and *Use as system prompt*
  modes — so a description like "For every message I send: `/rewrite`" turns a
  chat into a dedicated, single-purpose surface with no command to type. Only
  *active* skills expand; a reference is recognised only at a line start or after
  whitespace and when not followed by `/`, so paths (`/usr/bin`), URLs and prose
  (`and/or`) are never mistaken for a skill call. Nested references expand two
  levels deep and cycles terminate instead of recursing. **Assistant roles**
  expand references too, resolved in `resolve_role_prompt` so a role prompt
  behaves identically on topics, chats, workspaces, agents and Live sessions.
- **Agents are now definitions, and every start opens its own run.** Execution
  state — status, active prompt, transcript counters, token meter and the
  Copilot SDK session handle — moved off `AgentSession` onto a new `AgentRun`
  row, mirroring the `Workflow` → `WorkflowRun` pattern. Two workflows can now
  point a step at the **same agent and run at the same time**: previously the
  second start silently took over the first's live session, overwrote its
  status, wiped its artifact blackboard, wrote its step's capability overrides
  onto the shared agent row, and mis-attributed its tokens — and one agent going
  idle advanced *both* pipelines. Runtime registries, artifacts, events, tool
  grants, token accounting and the workflow advance seam are all keyed by run.
  Each run freezes the model, role, approval policy and capability toggles it
  started with, so editing an agent mid-flight primes the next run instead of
  moving the ground under the current one. Runs are kept as an audit trail and
  carry the `trigger` that opened them (`manual`, `workflow`, `schedule`,
  `webhook`, `fleet`, `retry`, `replay`). The agent's own columns remain as a
  write-through mirror of its current run, so the dashboard, inbox, metrics and
  command palette are unchanged. Fixes [#242](https://github.com/lrivallain/precursor/issues/242).
- **Agent run history.** An agent's insights sidebar gains a **Runs** rail
  listing recent executions — trigger, status, the workflow run that drove it
  and what it spent — with the current run highlighted; a workflow's step trace
  now names the agent run each attempt launched. New
  `GET /api/agents/{id}/runs` and `GET /api/agents/{id}/runs/{runId}` routes back
  it, `AgentSessionRead` embeds a nullable `current_run`, the artifact list
  accepts a `runId` filter, and the `agent.changed` / `read.changed` SSE events
  carry an `agent_run_id`.
- **Replay a single workflow step.** Every finished, agent-backed row in a run
  trace now carries a small replay icon that re-runs *that one step* on the
  exact input it first saw — the same kickoff preamble, brief and upstream
  hand-off — and advances nothing after it. The existing *Retry* is about
  recovering a **stopped run**: it only works on a failed or cancelled run and
  carries on through the rest of the pipeline, so there was no way to ask "what
  does this step do if I hand it the same thing again?" without re-running (and
  re-paying for) everything around it. Replay is offered on a step that
  **succeeded** too, which is the point: take another sample from a
  non-deterministic model, or check the step now that you've tightened its
  instructions or narrowed its tool servers. The new attempt lands in the same
  run trace badged `replay` and its spend rolls into the run total, but it is
  deliberately invisible to the coordinator — it never becomes "the attempt that
  failed" for a later retry, never counts as a pipeline attempt, and is never
  mistaken for a stalled step by the watchdog. Refused while the run is still in
  flight, since a live run owns its steps' agents.

- **Pick which MCP servers a workflow step may use.** A step's *Tools* toggle
  was all-or-nothing: on meant the entire enabled catalogue. Tool schemas are
  re-sent on every turn, so on a modest install that is a six-figure token bill
  per round-trip for a step that needed one server — in a measured six-step
  briefing run the tool-using turns accounted for 2.28M of 2.34M tokens while
  each step actually needed exactly one. The step modal now has a **Servers**
  row listing every enabled server with its tool count: leave it on **All** for
  the old behaviour, name the ones the step may see, or select none to run it
  with no tools at all. Precursor's own server is listed and scoped alongside
  the rest — it needs no enabling, but it is one of the larger catalogues on a
  normal install and a step that only needs `fetch` shouldn't pay for topic,
  memory and schedule tools it will never call. It is a real allowlist rather
  than a prompt-level request — the servers you didn't pick are never attached,
  so the step cannot reach them and pays nothing for their schemas. Changing the
  selection rebuilds that step's session, and unknown names are carried through
  export/import so a workflow still imports onto a machine with a different
  server set.

- **Workflows remember things between runs, and steps can read them.** A pipeline
  now has its own **state**: named values scoped to the workflow, shared by every
  step and kept **across runs**. Everything else a step could see described a
  single execution — the run brief, the run trace, and an artifact blackboard
  that's wiped between runs — so a scheduled pipeline had nowhere to record what
  it must not redo next time. Agent state can't stand in for it: a step points at
  a *reusable* agent, so a cursor written under the agent's own scope is shared
  with every other pipeline using that agent, and an inline agent's scratchpad
  dies with its step.

  Steps **read** state through `{{state.<key>}}` placeholders substituted into
  their instructions before the agent ever runs, and **write** it with the
  `workflow_state_set` tool (plus `_get`, `_list`, `_delete`), which resolves the
  owning pipeline per call rather than from the environment — the right answer
  for an agent shared by several workflows. The new **Pipeline state** panel on
  the workflow page lists what's saved, expands a value, and lets you seed a
  starting cursor by hand or reset one that's gone bad
  (`GET|PUT /api/workflows/{id}/state`, `DELETE .../state[/{key}]`).

- **Step instructions support placeholders.** `{{run.input}}` and
  `{{step.N.output}}` were documented on the model but never actually
  implemented — a step written with them was handed the raw template. They now
  resolve for real, alongside `{{state.<key>}}`, each with an optional `| default`
  fallback (`{{state.cursor | the beginning of time}}`) that makes a first run
  safe. An unresolved placeholder renders as `(unset)` rather than a silent blank,
  and anything we don't recognise is left untouched. `{{step.N.output}}` pairs
  with `context_mode: none` to feed a step exactly one earlier output instead of
  the whole upstream transcript. A worked example ships at
  `examples/workflows/stateful-digest.yaml` — import it from **Workflows →
  Import**.

- **Agents can remember things between runs.** A new **State** store gives every
  agent a private key/value scratchpad that *survives re-runs* — the missing
  third surface next to long-term **memory** (global, and injected into every
  turn) and **artifacts** (per-agent, but wiped at the start of each fresh run).
  A scheduled or webhook-triggered agent can now save its cursor — the last id it
  processed, the items it already saw — and resume next run instead of redoing
  the work. Agents read and write it with four first-party MCP tools
  (`state_list`, `state_get`, `state_set`, `state_delete`) that default to the
  calling agent, and **only the key index reaches the prompt**: bodies stay in the
  database until a tool asks for one, so a large saved cursor costs nothing per
  turn. The insights sidebar lists the saved keys, expands one to show its body,
  and offers per-key delete plus a reset for when a cursor goes bad
  (`GET|PUT /api/agents/{id}/state`, `DELETE /api/agents/{id}/state[/{key}]`).
  Serving the tools to *external* MCP hosts is a separate opt-in under
  **Settings → MCP servers → Precursor capabilities → Agent state**.

- **See what a workflow step actually did.** Every attempt in the run trace now
  carries an **Activity** section rendering the same timeline the Agents cockpit
  does — tool calls with their arguments and output, reasoning, assistant
  messages, lifecycle hooks — sliced to that attempt's own window (events are
  archived per agent, so a step re-driven four times shares one stream). Before
  this, a step that blocked or stalled having produced no output left literally
  nothing to diagnose from. In a finished attempt a tool call that never
  terminated is reported as *interrupted* — or *never approved* when it stopped
  at a permission gate — instead of spinning as though it were still running.

- **Retry a single failed step.** The step whose failure stopped a run wears a
  **Retry this step** button on its own card: it re-drives that step as a fresh
  attempt on the **same run** and carries on from there, instead of forcing a
  re-run of the whole pipeline that discards every good step before the bad one
  (and pays for them twice). The attempt is appended to the run trace with a
  bumped attempt badge, like a gate loop-back, and the step's automatic retry
  budget resets so the manual retry doesn't inherit a spent counter.

- **Answer a blocked step when resuming it.** A run parks when a step's agent
  raises a question it can't resolve alone; resuming re-drove it blind, straight
  back into the same question. The Resume control now turns amber and opens a box
  showing what was asked, and the answer is injected into the step's kickoff.
  Both `POST /resume` and the new `POST /retry` take an optional `{"input": …}`.

- **A workflow-wide tool-approval policy.** Settings → Workflows gains **Tool
  approvals** (Manual / Balanced / Autonomous), applied to whichever step's agent
  is about to run rather than written onto the shared agent row. This is what
  makes an *unattended* pipeline actually unattended: a step that stops at a
  permission gate parks the whole run until a human answers, which a scheduled or
  webhook-fired workflow has nobody to do.

- **A tool-permission gate no longer parks the whole run.** Waiting on a tool
  decision was treated as a *block*: the workflow paused, the step's trace was
  closed, and a manual **Resume** was required — so an agent making five tool
  calls in one step blocked five times, stacking five "Blocked" attempts in the
  trace. The turn is in fact still alive and continues by itself the moment the
  gate is answered, so the run now stays `running` with its trace open and only
  the approve/deny card is surfaced. A question the agent genuinely *raised*
  (`blocked`) still parks the run, as before. The step timeout still applies, so
  a gate nobody ever answers is caught by the watchdog instead of parking the
  pipeline forever.

- **Approve a step's permission request from the workflow board.** A parked
  tool-permission gate now renders its approve/deny card on the board itself.
  This was previously impossible to answer at all for an **inline** step, whose
  agent is hidden from the Agents roster — so the run simply stalled until the
  watchdog killed it. Answering also puts the paused run back in flight: the
  block had stopped the coordinator (`advance_for_agent` only looks at *running*
  workflows), so resolving the gate alone would leave the approved agent
  finishing its turn into a pipeline that had stopped listening — the board stuck
  on "Blocked" and the same request raised again on every reload. A decision the
  runtime can no longer match is now reported as a conflict instead of silently
  doing nothing.

- **Import and export agents and workflows as YAML.** A workflow exports to a
  single, readable file — its steps, its wiring, and every agent those steps use
  — so a pipeline can be committed next to the project it automates or handed to
  someone else; an agent exports on its own the same way. What travels is the
  *definition*: runtime state (status, run history, artifacts, token counters,
  the SDK session handle) is left out, webhook tokens are never exported because
  they're per-install credentials, and a carried schedule arrives **paused** so a
  shared file can't start firing on its new owner. Importing is two-phase, since
  the interesting decision only exists once collisions are known: a preview
  reports them without writing anything, then each colliding agent is resolved as
  **use existing** (reference it untouched), **replace** (overwrite its
  definition in place, so every other workflow using it follows — with the blast
  radius shown up front) or **create new** (keep both). Objects are stamped with
  a stable portable id on first export, so re-importing a file that came from
  this install updates the object it came from rather than guessing from the
  name; a bare name match defaults to the non-destructive choice instead, which
  also makes scripted imports safe. New `/api/transfer` router, and
  `export_id` columns on `agent_sessions` and `workflows`.

- **Create a reusable agent from the step editor.** An Agent step now chooses
  between **Existing agent** (pick one from the Agents section) and **New agent**
  (name it and give it an objective right here) — so building a pipeline no
  longer means breaking off to create its parts first. The agent lands in the
  Agents section on save, editable and pickable by other steps and workflows;
  reopening the step then shows it as a plain reference, because creating is a
  one-time act rather than a mode a step stays in. Backed by
  `WorkflowStepInput.reusable`, which decides whether a step-authored prompt
  mints a real agent or the step's private vessel — omitting it keeps the vessel,
  so existing payloads are unchanged.

### Changed

- **An installed Precursor now stores its data in a per-user directory.**
  `PRECURSOR_DATABASE_URL` and `PRECURSOR_DATA_DIR` defaulted to paths relative
  to the working directory (`./precursor.db`, `./.precursor`), which is exactly
  right for a checkout — it is what makes each clone and worktree an isolated
  sandbox — but wrong for an installed wheel, which a launcher starts with its
  working directory set to `/`. That silently produced a fresh, empty database
  wherever it happened to launch from.

  The defaults now depend on how Precursor was installed: a source checkout
  keeps the relative paths, while a wheel resolves to
  `~/Library/Application Support/Precursor` (macOS),
  `$XDG_DATA_HOME/precursor` (Linux) or `%APPDATA%\Precursor` (Windows). Setting
  either variable explicitly overrides both, so existing configurations are
  unaffected.

### Fixed

- **`mcp` was uncapped, so a fresh install resolved a wheel the app cannot
  import.** `mcp` 2.0 renamed the client entry points Precursor imports
  (`streamablehttp_client` → `streamable_http_client`) and dropped the old
  names, and the `>=1.28` floor accepted it — so `uvx precursor-ai` on a machine
  resolving today's index got an instance that died on startup with an
  `ImportError`. Capped to `>=1.28,<2`.

  Worth noting the shape of this: `uv.lock` pins exact versions for dev and CI,
  which is why the suite stayed green, but **end users of the published wheel
  resolve fresh** and the lockfile never reaches them. Coarse floors are fine
  for compatible releases; an API-breaking major needs a real cap.

- **The supervisor read settings from the caller's directory, not the
  instance's.** `pydantic-settings` resolves `.env` relative to the process
  working directory, but the CLI and the tray can be invoked from anywhere while
  the instance always runs in its own working directory. Running
  `precursor service start` from `~` therefore missed the `.env` beside the
  data, fell back to the default port, and passed the child an explicit
  `--port` that overrode the `.env` it would otherwise have read — silently
  moving an instance configured for `:9000` onto `:8000`. Settings are now
  resolved as the child will see them, and the foreground path adopts the
  working directory before reading them.

- **Installing the login item while an instance was already running caused a
  crash loop.** launchd's `RunAtLoad` (and systemd's `--now`) start the instance
  as part of registering it, which raced the start `service install` did itself;
  the loser hit `--strict-port` and exited non-zero, which `KeepAlive` reads as
  a crash and retries every 30 seconds forever. The foreground path now yields
  to a healthy existing instance and exits *successfully*, and `service install`
  waits for the unit to come up instead of racing it.

- **Archived agent events could grow without bound.** The event normaliser
  capped captured tool I/O only when the value was already a string; anything
  rendered through `jsonify` — notably a hook's `input`, which re-embeds the
  entire tool result the completion event *already* stored — went to the
  database uncapped and pretty-printed. An event's free-text body wasn't capped
  at all, so every session start archived the full system prompt, hundreds of
  times over. On one production database these two paths held **45 MB of a
  126 MB file**. All captured values are now capped uniformly and marked when
  trimmed, with a much tighter limit for system prompts (boilerplate) than for
  conversational text (which the timeline renders in full). A matching
  **Oversized agent events** cleanup target re-applies the caps to rows already
  written, since retention can't reach them — an oversized payload isn't
  necessarily old, and counts as a single row against a per-session ceiling
  however many KB it holds. It preserves every timeline node, tool name and
  status, and is idempotent.

- **An agent no longer forgets what it is halfway through a conversation.** Its
  instructions (`task_prompt`) are delivered once, as the first message of the
  SDK conversation, so they survive only as long as that conversation does. The
  runtime deliberately tears the session down and rebuilds it on an expiring
  OAuth bearer, a changed MCP catalogue or a recovered sign-in — and that rebuild
  is meant to be invisible, resuming the same conversation via
  `copilot_session_id`. Except the handle was read off the session object
  (`getattr(sdk_session, "id"…)`), and `CopilotSession` exposes no such
  attribute: it resolved to `None` every time, so no run ever stored one. The
  rebuild therefore passed no `session_id` and opened an *empty* conversation,
  silently discarding the task prompt and the entire history. Symptom: an agent
  answers its first turn perfectly, then replies to the next message as though it
  had never been briefed — the telltale being a `session.start` event appearing
  mid-thread, right before a user message. It bit conversational agents hardest,
  because a WorkIQ credential refreshing between turns is enough to trigger it.
  The handle is now captured from the `SessionStartData` event, which is the only
  place the SDK publishes it, and never overwritten once set.


  startup but not their expiry, and its `is_token_valid()` treats an unknown
  expiry as valid — so a credential read back from the database always looked
  fresh, the silent-refresh branch that check guards was never entered, and the
  401 that eventually followed escalated straight to a full browser grant, which
  never attempts a refresh either. The refresh token was dead weight, and every
  access-token expiry became an interactive sign-in. The trace added above is
  what caught it: over 401 recorded events, **58 escalations to a full
  authorization and zero refresh attempts**, against credentials that held a
  refresh token throughout. Precursor already records when each token was issued,
  so it now restores the real expiry (less a minute of skew) as the credential
  loads, and the SDK's own refresh path works as designed. A legacy token with no
  recorded issue time is still assumed valid rather than forced through a sign-in
  that may not be needed.

- **Startup migrations no longer silence the app's own logs.** `init_db` runs
  `alembic upgrade head` on every boot, and Alembic's `env.py` applied
  `alembic.ini`'s logging with `fileConfig`'s default
  `disable_existing_loggers=True` — which switched off every `precursor.*` logger
  created at import time, replaced the unified handler and formatter, and pulled
  the root level down to `WARN`. From the first migration onwards the backend was
  substantially quieter than configured, which is why raising `--log-level` often
  changed so little. Alembic's own logging config is now applied only when it's
  driven from its CLI, and never disables existing loggers.

- **A failed automatic re-auth no longer costs you the credential.** The
  hands-free passes clear the stored token before running, so the SDK is forced
  through a fresh grant instead of short-circuiting on one it still considers
  valid — but if the pass then failed, that credential was simply gone. Since the
  verdict that triggers a pass can come from a transient 401, a refresh token
  that would have worked on the next try was being thrown away, turning a blip
  into a mandatory interactive sign-in. The old credential is now restored
  whenever a hands-free pass doesn't complete, at a cost of at most one doomed
  refresh later. An explicit **Sign in** still clears outright.

### Removed

- **The GitHub Models provider, and the retirement machinery built around it.**
  When GitHub shut the service down the provider was kept behind a generic
  `retired` flag so anyone still pointed at it got an explanation rather than a
  raw `410 Gone`. The provider saw little use, and keeping a whole mechanism
  alive to describe something that no longer exists costs more than it returns.
  Gone: `GitHubModelsProvider`, its `ProviderSpec` entry, the `retired` field on
  the spec/schema/TS types, the picker filtering and the 502 in the catalog
  route, and the *(retired)* labelling in Settings.

  A migration repoints any install still set to `github_models` at **GitHub
  Copilot**, which authenticates with the same GitHub token — without it, an
  unknown provider id resolves to no spec and falls back to the **mock**
  provider, which would answer with convincing fake replies instead of failing.

### Changed

- **The reference section was audited against the code.** Every documented
  `PRECURSOR_*` variable was diffed against `config.Settings`, every cited file
  path and table name checked for existence, every route and SSE event name
  compared with the live OpenAPI schema, and the plugin contract read back
  against `plugins/registry.py`. The env surface, the twelve SSE events, the ten
  built-in MCP servers and the dependency list all matched exactly. Four things
  did not, and are fixed:

  - the API surface listed a **`schedules`** area as though it were a top-level
    router, when recurrence is a sub-resource of the thing being scheduled
    (`/api/topics/{id}/schedule`, `/api/agents/{id}/schedule`);
  - the **`drawio`** router was undocumented;
  - **`GET /raw/{slug}/{path}`** — read-only, unauthenticated, and the one route
    outside `/api/*` — was missing from a page that states the API lives under
    `/api/*`;
  - the architecture reference's database highlights omitted **`AgentState`** and
    **`WorkflowState`**, the two durable stores that survive a re-run, even
    though both have their own feature pages.

  The configuration reference's opening also claimed a Settings-backed value has
  *no* environment twin, full stop; there are two justified exceptions, which the
  page already documented at its foot, so the claim now points at them.

- **The agents guide is five pages instead of one long scroll.** It was the
  longest page in the docs — enabling, the dashboard, fleet orchestration,
  artifacts, durable state, autonomous missions, approvals and scheduling in a
  single column. It now follows the same shape as the workflows guide: an
  **overview** (what agents are, enabling, the dashboard) plus **Running an
  agent**, **Orchestrating a fleet**, **Artifacts & state** and **Autonomous
  missions**. The `/features/agents-mode` URL is unchanged, and the eight
  in-page anchors other pages deep-link to were repointed at the section that
  now owns each one.

- **Workflows are no longer flagged as work in progress.** The overview carried a
  warning that the surface was "still evolving — expect the UI and controls to
  keep changing", which undersold a feature that now has gates, loop-back, human
  approval checkpoints, pipeline state, per-step tool allowlists, run replay and
  a stall watchdog. The notice is gone; the *Experimental* marker on autonomous
  agent missions and the plugin system's "not yet tested" warning are unaffected.


- **The quick start covers every section, not just topics.** It walked you from
  install to a first topic-scoped conversation and stopped, so the five sections
  that need a first step of their own — and in three cases a prerequisite — were
  left to the feature guides. It now adds *Start with the other sections* with a
  sub-section each for **Agents**, **Live sessions**, **Workflows**, **Kanban**
  and **Files**, leading with the prerequisite that actually blocks you (the
  `agents` extra, Azure Speech credentials, a global repo plus `read:project`).

  Writing it turned up two docs bugs, now fixed: workflows were described as
  independently "opt-in and off by default" when they're gated on the **Agents**
  toggle with no switch of their own, and a "local" workspace was described as
  pointing at files already on disk when it in fact **creates an empty folder**.

- **The docs are a third shorter, and no longer describe a topics-only app.** The
  guides had accreted detail that churns faster than anyone can maintain it — UI
  chrome ("cards lift on hover", "a warm gradient wash"), implementation internals
  (`IssueContextCache`, table names, `services/*.py` paths), and design rationale
  that belonged in a changelog rather than a guide. `features/mcp.md` spent ~180
  lines on WorkIQ OAuth internals — loopback port numbers, `prompt=none` iframes,
  orphaned-flow preemption — none of which a reader can act on; it is now one
  section that says sign-in renews itself and points at the config flags. Every
  page keeps what it is, why you'd use it, how to do the thing, and the gotcha
  that bites.

  The narrative was also stale: the introduction opened "Precursor is an AI
  assistant that keeps each thread of work in its own **topic**" and relegated
  meetings, agents and workflows to what it "layers around that core" — framing
  from when topics were the only feature. Precursor is now described as several
  surfaces sharing one set of tools, personas and memory.


- **The agents feature page is `agents-mode.md`.** On macOS and Windows the
  filesystem is case-insensitive, so `website/features/agents.md` *is*
  `AGENTS.md` — the filename coding agents treat as an instruction file. Every
  agent opening this repo was loading a 550-line product page as if it were
  repo instructions. The page moved to `/features/agents-mode`, and every
  internal link, the sidebar and the landing grid moved with it. There is
  deliberately no stub left behind at the old path, since that would recreate
  the collision.
- **Documentation screenshots are reproducible, and six pages now have one.**
  Workflows — the largest feature in the app — had no screenshot at all, and
  neither did the scheduler, skills, command runner or import/export pages.
  Retaking a shot previously meant pointing a browser at your own instance,
  which is exactly how a real account or a real repository ends up in the docs.
  Three scripts now do it from a clean room: `scripts/seed_demo.py` builds a
  throwaway database of invented fixtures (and refuses to run unless the
  database URL says `demo`), `scripts/demo_server.sh` serves it with `gh`
  stripped from `PATH` so the persona can only resolve to "Guest / Not
  connected", and `scripts/capture_screenshots.js` drives each scene and writes
  the light and dark variants at `deviceScaleFactor: 2`. Added shots for the
  workflow board, a workflow run trace (with a gate's loop-back and its
  `attempt 2`), the recurrence editor, Settings → Skills, Settings → System's
  command-runner jail, and the workflows gallery.

- **The workflows guide is four short pages instead of one 786-line wall.** The
  page had accreted a feature at a time until it was by far the longest in the
  docs — 30 headings covering authoring, execution, cost, failure handling and
  the REST surface in one scroll, with no screenshot to break it up. It is now an
  **overview** (what a workflow is, how it differs from agent `depends-on`, the
  four step kinds, the autonomy rule) plus **Building a pipeline**, **Configuring
  a step**, **Running a pipeline** and a **Reference**. The `/features/workflows`
  URL is unchanged; deep links from the agents and MCP pages were repointed at
  the section that now owns each anchor. Also fixes a markdown bug that had
  silently merged the "Manual" and "Schedule" trigger bullets into one line.
- **Workflows are listed where the other sections are.** Workflows mode was
  missing from every "what's in the app" list except the landing feature grid —
  the sections table in the feature guides, the introduction, the quick-start's
  home-card list, the README highlights, and the API reference's router table all
  described a six-section app. Added to each.
- **Assistant roles are documented.** A role had a Settings tab, a `/role`
  command, a per-collection default and a per-workflow override, but no
  documentation at all — and two pages linked to a `/features/roles` page that
  never existed (one of them a dead link, the other pointing at Topics). They now
  have a section on **Skills, roles & memory**, which the sidebar and both links
  now name.
- **The API and configuration references cover the whole surface.** The API
  reference's router table described 13 of 31 routers — `workflows`,
  `workspaces`, `live`, `reminders`, `roles`, `search`, `stats`, `commands`,
  `attachments`, `projects`, `llm` and `refine` were absent — and listed 5 of the
  12 SSE events. The configuration reference was missing 12 `PRECURSOR_*`
  variables (`CORS_ORIGINS`, `DATA_DIR`, the four scheduler/reminder cadences,
  `MCP_IDLE_TTL_SECONDS`, `GITHUB_MCP_TOOLSETS` and four WorkIQ keep-alive
  knobs), and still closed with a "runtime settings layer over env defaults" note
  that contradicted the no-env-twin rule stated at the top of the same page. A
  cross-check against `config.Settings` now shows no drift in either direction.

- **Settings you can change in the app no longer have a hidden env twin.**
  `config.Settings` sets `env_prefix="PRECURSOR_"`, so every field on it silently
  became a `PRECURSOR_*` variable — including 24 that already had a control in the
  Settings panel. None were documented, so the only way to find one was to read
  `config.py`, and two of them (`PRECURSOR_CMD_RUNNER_JAIL`,
  `PRECURSOR_CMD_RUNNER_NETWORK`) were an undocumented second way to switch off
  the command-runner sandbox. The factory default for each now lives as a constant
  beside its `resolve_*` helper, leaving the DB row — and the panel that writes it
  — as the single way to set the value. `.env` keeps only what must be known
  before the database exists: bind address, database URL, data directory, ticker
  cadences. A test pins the split so it can't creep back. Affected:
  `LLM_MAX_INPUT_TOKENS`, `LLM_MAX_TOOL_RESULT_TOKENS`,
  `SCHEDULED_RUN_TIMEOUT_SECONDS`, `TOOL_RESULT_RETENTION_DAYS`,
  `LIVE_TRANSCRIPT_RETENTION_DAYS`, the eight `CMD_RUNNER_*`, the four `AGENTS_*`
  defaults, the four `WORKFLOWS_DEFAULT_*`, and the three `BACKUP_*`.
  `PRECURSOR_WORKIQ_TENANT_ID` and `PRECURSOR_PLAYWRIGHT_BROWSER` are kept
  deliberately: both describe the machine or tenant rather than a preference, and
  both are documented.
- **Installing the `agents` extra is now the opt-in.** Agents mode took two steps
  — install a ~150 MB extra, then find a toggle in **Settings → Agents** — so the
  people who had just paid for the payload still saw nothing until they went
  looking. It now follows the capability probe: on once the Copilot CLI runtime
  resolves, off (with the existing "install this" explanation) when it doesn't.
  The toggle stays as the one control on top of that — it is the kill switch and
  the spend control, and still reconciles the runtime live without a restart.
  There is deliberately no env-level default to go with it: the probe already
  answers "can this run?" and the toggle answers "should it?", so a third input
  would only have contradicted the premise that installing the extra *is* the
  opt-in. The probe is still checked at every use, so a stored `true` on a
  machine without the SDK stays inert.
- **An agent is addressed by its own `public_id`, not by a session handle.**
  `AgentSession.copilot_session_id` doubled as the agent's URL identity and as
  the Copilot SDK handle for whatever was running; the handle now belongs to the
  run, so the agent gained a stable `public_id` in its place. Existing UUIDs are
  carried across by the migration, so `/agents/{uuid}` deep links, `/agent
  <uuid>` nudges and transfer lookups keep resolving.
- **A running workflow now wears the holographic frame on the gallery.** The
  Workflows landing signalled a live run only with a small status dot and a
  Pause button, so a card mid-flight read the same as an idle one at a glance —
  yet everywhere else in Precursor an in-flight turn is the rotating iridescent
  ring: the composer while a reply streams, and the step cards on the workflow
  board. Cards whose workflow is `running` now use that same frame, so the one
  thing worth spotting on the page is the one thing that moves.

- **"Inline prompt" is no longer offered on an Agent step.** It produced exactly
  what the **Inline** step type produces — the same hidden vessel, the same
  runtime, differing only in the badge on the board — so the same intent had two
  spellings and the step type disagreed with the toggle beneath it. One-off work
  is now the **Inline** kind, full stop. A **Gate** keeps all three sources,
  since there is no inline gate kind for its one-off check to be. A blank step
  starts as **Inline**, which is where it effectively started before.

### Fixed

- **A new topic lands in the collection you're looking at, and the form says
  which.** The create form never sent the active collection, so every new
  top-level topic silently fell back to the default one — leaving you to move it
  by hand each time, or to wonder why it hadn't appeared in the tree at all. It
  now carries a **Collection** field, preselected to the collection you're
  viewing, so the destination is visible and changeable *before* the topic
  exists rather than being an invisible consequence of the sidebar. Picking a
  parent scopes that field to the parent's collection instead, since a subtree
  lives in exactly one. Two other paths were worse and left the membership
  *null*, which no collection filter matches, so the topic was invisible in the
  sidebar until you found it through search: promoting a chat to a topic, and
  the MCP `create_schedule` tool. Both now resolve a collection
  (`?collection_id=` and a `collection` argument respectively, defaulting to the
  protected default), and a migration re-homes any topic already stranded that
  way. `GET /api/topics` and `GET /api/topics/archived` gained the
  `?collection_id=` filter the tree endpoint already had.

- **A subtree can no longer end up split across two collections.** Saving topic
  settings sends the parent and the collection together, so a re-parent could be
  overruled by the collection dropdown's stale value and drop the topic into the
  wrong collection. The two are now reconciled by *what changed*: re-parenting
  adopts the new parent's collection, and moving a sub-topic to another
  collection promotes it to a top-level topic (taking its own children) rather
  than leaving a branch straddling both.


  `body` parameter was string-only, so a model reaching for the object the target
  endpoint documents — the natural move — hit a validation error whose shape it
  couldn't see, and retried its way through progressively stranger encodings. It
  now accepts an object or array and serialises it, setting
  `Content-Type: application/json` unless the caller supplies one, so a raw
  string still behaves exactly as before.
- **A workflow step is no longer told about state tools it doesn't have.** The
  "this workflow's saved state" block — which instructs the agent to call
  `workflow_state_get` — was attached whenever a step had MCP on at all, ignoring
  the step's own server allowlist. A step scoped to, say, `workiq` was handed an
  index it had no tool to read, and spent a turn explaining it couldn't comply.
  The block now follows the same predicate the attach path uses.
- **The workflows guide no longer documents a feature that never shipped.** Its
  overview compared workflows to agent `depends-on` links "agents can already
  declare" — but the per-agent dependency table (`agent_deps`) was dropped in the
  same commit that introduced Workflows, so no such link has ever existed for
  users. The section is now *What the workflow owns*, stating plainly that agents
  can't trigger each other and that all chaining lives on the workflow.
- **A failed turn no longer reads as a success.** Stream errors are persisted as
  system messages, and *every* system message rendered as a green check-marked
  acknowledgement — so "The model provider rejected the request" looked exactly
  like "Run now accepted". Error notices now render red with a warning icon (and
  an `alert` role), while acknowledgements keep the green treatment.
- **Workspace chat now honours the context-budget settings.** `POST
  /api/workspaces/{slug}/chat` trimmed its prompt using the env-level token
  limits while topic and chat turns resolved them from the database, so changing
  **max input / tool-result tokens** in Settings silently had no effect on
  workspace conversations — the one surface most likely to hit the ceiling, since
  it reads files.
- **The test suite no longer spawns the real Copilot CLI.** `make check` took
  ~9 minutes on machines that had ever run `make dev`, and ~75s on machines that
  hadn't — the same code, six times slower for exactly the people running the
  app. App startup starts the agents runtime when the optional
  `github-copilot-sdk` is importable *and* the persisted `agents_enabled` flag is
  on; exercising Agents mode means turning that flag on, and it persists in the
  session-wide scratch database, so those tests — and every later app startup
  while it stayed on — spawned and tore down the bundled native CLI. At ~12.8s
  each the suite sat **87% idle** (70s of CPU for 539s of wall clock), which read
  as "the tests are slow" rather than as a process spawn. Tests now report the
  agents runtime as unavailable by construction, so timings no longer depend on
  which extras a developer happens to have installed.
- **A workflow board shows its own run.** The step strip was rendered from the
  shared agent row, which mirrors that agent's *current* run — so two pipelines
  driving one agent both displayed whichever finished last, right down to the
  answer and the question waiting on you. Each board now resolves its step
  through the run that step actually launched.
- **The agent transcript reads one run at a time.** Concurrent drivers produced
  a single interleaved timeline — two prompts and two answers with nothing
  saying which belonged to which. Every event now carries its run and the
  transcript **defaults to the latest one**, following along when the agent
  starts again. The **Runs** rail doubles as a filter (**All runs** restores the
  full history) and a chip above the timeline says which run you are reading.
  Agent-wide notices (the MCP authorisation banner) carry no run and so appear
  only in the unfiltered view.
- **A run records what actually started it.** Every entry point outside a
  workflow opened its run as `manual`, so the Runs rail credited a human for
  schedules, webhook fires, fleet releases and retries alike.
- **A gate agent reads its own verdict.** The event archive spans every run of
  an agent — that is what makes it the transcript you scroll — so "the newest
  assistant message" meant "whichever concurrent run spoke last". A gate shared
  by two workflows could hand one pipeline the other's `PASS` and wave a failing
  deliverable through. Verdict and hand-off reads are now scoped to the run that
  produced them; the agent-wide read remains for the transcript itself.
- **A crash no longer leaks a concurrency slot.** Boot recovery flagged only the
  agent rows it found mid-turn, leaving the runs the fleet governor counts stuck
  at `running` forever. After `agents_max_concurrent` hard stops, nothing could
  start again.
- **Signing in to an MCP server rebuilds the right sessions.** The rebuild swept
  the live-session registry as though it were keyed by agent when it is keyed by
  run, so it skipped the sessions that needed rebuilding and could disconnect an
  unrelated agent's live run mid-turn.
- **Replaying a step of an edited workflow finishes.** If the step had been moved
  or removed from the definition since the original run, the replay opened no
  execution to attach to and its trace row could never be closed — leaving the
  step pinned "in flight" on the board. The replay now always opens a run,
  falling back to the agent's current settings when the step is gone.
- **The concurrency governor counts executions, not agents.** A slot exists to
  bound how many Copilot SDK sessions run at once, and there is now one session
  per run — so two workflows driving the same agent burned two sessions while
  charging `agents_max_concurrent` for one. The fleet count, and the
  `running_now` gauge in the Agents header, now read the runs.
- **Fleet token totals and the inbox's "budget" badge read every run.** Both
  summed the agent's own counters, which mirror its *current* run — so lifetime
  spend collapsed to the latest turn of each agent, and an agent parked by the
  budget governor after several runs was mislabelled as having raised a
  question.
- **The stall watchdog watches runs, not agents.** It selected sessions whose
  status was `running` and read their last activity — both of which now mirror
  only the agent's *current* run. A turn wedged by a hung tool therefore became
  invisible the moment another execution took over, and stayed pinned in
  `running` forever: never surfaced for a Resume, and permanently holding one of
  the `agents_max_concurrent` slots. It now sweeps the run rows, interrupts the
  run itself, and drops only *that* run's live session — previously it tore down
  every session the agent owned, killing a healthy concurrent workflow's
  in-flight turn as collateral.
- **Raising a token budget no longer un-parks an agent that is still over it.**
  The check compared the new ceiling against the agent's own counters, which
  mirror its current run, so a raise above the latest turn's spend flipped a
  budget-blocked agent to idle even when its lifetime total was still past the
  limit — and the governor simply re-parked it on the next metered round. It now
  weighs cumulative spend across every run, matching the governor.
- **An agent's token budget is no longer reset by starting it again.** The
  governor read the agent's cumulative counters, which are now a mirror of its
  *current* run — so every new run would have handed the agent a fresh
  allowance, and two concurrent runs would each have seen only their own spend.
  It now sums across every run of the agent, which is what "cumulative
  governance on the definition" was always supposed to mean.
- **Editing a workflow step over the API no longer deletes its inline agent.**
  `PATCH /api/workflows/{id}` with a step that names its private vessel via
  `agent_id` but leaves `task` out — a partial edit, say to narrow the step's
  server allowlist — attached the step to that vessel and then swept the vessel
  up as an orphan in the same request. The call returned `200` with `agent_id`
  still in the body, so nothing signalled the loss: the step was left pointing
  at a deleted agent, and the prompt was gone. Referencing a vessel now *claims*
  it, which makes the endpoint idempotent for partial step edits; the prompt is
  simply left as it was. The builder was never affected — it resends the prompt
  on every save — but scripts, direct REST calls and agents driving a pipeline
  were, and a single edit could wipe every inline prompt in a workflow. The
  sweep also detaches any step still referencing a vessel before deleting it,
  rather than trusting `ON DELETE SET NULL`: SQLite runs with foreign keys off,
  so that dangling id survived — and SQLite may later hand the same id to an
  unrelated agent.

- **WorkIQ sign-ins now ask for a refresh token, so they can be renewed
  unattended.** The OAuth flow never requested `offline_access`, so Entra
  returned an access token with **no refresh token** — a terminal credential. The
  background keep-alive would try to renew it, fail, and the only way back was a
  human at a browser, which is exactly what a scheduled workflow or a
  backgrounded agent doesn't have; overnight runs came back having quietly
  answered without their tools. Because the MCP SDK overwrites the requested
  scope during discovery, `offline_access` is merged into the finished
  authorization URL — the last point before the browser — alongside whatever
  scope discovery resolved. Both built-in profiles are covered. Tokens stored
  *before* this can't gain a refresh token retroactively, so instead of
  attempting a renewal that cannot succeed, Precursor now spots the missing
  refresh token and raises the ordinary sign-in banner straight away: you
  re-authenticate **once** and come back with a renewable credential. Depending
  on how the WorkIQ preview client is registered, that sign-in may show a one-off
  consent screen for the new `offline_access` permission. (The Agent 365 pair
  requests `<resource>/.default`, which replays already-consented permissions, so
  it was getting a refresh token by accident and is unaffected.)

- **An agent that lost an MCP sign-in now picks it back up.** When a server was
  enabled but couldn't be authenticated, the session was built without it — and
  the reuse check tracks the set of *enabled* servers (deliberately, to avoid
  looping against a server that can't authenticate), so signing back in changed
  nothing it looked at. The tool-less session was reused indefinitely and the
  only cure was restarting Precursor. The live session now also records a digest
  of the stored credential for each server it had to skip, and rebuilds when that
  digest changes. It stays loop-free by construction: a rebuild that still can't
  attach records the same digest and doesn't fire again until fresh tokens are
  actually persisted, and the credential is never held in memory in the clear.

- **A workflow step whose named server never attached no longer reports
  success.** A step that allowlisted a server it couldn't sign in to ran anyway:
  the agent answered from its own knowledge, rested idle, and the coordinator
  read idle as done and marked the step **completed** — the worst outcome, since
  the run looked fine. Naming a server in a step's allowlist is a hard
  requirement, so the turn is now parked **Blocked** naming the server(s), which
  pauses the run for a sign-in and a **Resume**. Scoped to explicit allowlists on
  purpose: an agent running the whole enabled catalogue asked for whatever was
  available, not for that server in particular, and is left alone.

- **The test suite no longer calls a real model.** Three live-meeting tests
  (`ask`, `summary`, `summary/from-transcript`) never stubbed a provider — they
  relied on `get_llm_provider` falling back to the offline mock, which only
  happens when no GitHub token resolves. Signed in to `gh`, they issued live
  GitHub Models requests and failed against an entitlement the token doesn't
  have, so `make check` passed or failed depending on the developer's login
  state rather than on the code. Tests now run with no LLM credentials by
  construction.

- **Importing a workflow now warns about servers it can't attach.** A step's
  server allowlist travels verbatim, which is what lets a workflow move between
  machines — but on an install missing one of those servers the step just
  quietly ran with fewer tools than its author gave it, and the symptom only
  showed up much later as odd behaviour. The import preview now names them
  before you commit, separating "not installed here" from "installed but
  switched off in Settings → MCP", which is the one you can fix without leaving
  the app. The step editor makes the same distinction in colour: red for a name
  this machine doesn't know, amber for one that's a switch away from working.

- **The step editor's server picker now explains itself when it's empty.** With
  no MCP servers enabled, the **Servers** row offered nothing but **All** and
  gave no hint why, which read as a broken control rather than a setup step. It
  now says so and points at Settings → MCP, and it tells "still loading" apart
  from "nothing enabled" so the message doesn't flash on open. A server named by
  a step but switched off locally also reports that, instead of claiming it
  isn't installed.

- **An agent with tools off no longer rebuilds its session on every dispatch.**
  The reuse check compared the session's stored MCP fingerprint against the
  *enabled catalogue*, but a tools-off agent stores an "off" sentinel — so the
  two could never match and the Copilot session was discarded and rebuilt each
  time the agent ran, throwing away its warm state for nothing.

- **A workflow step no longer runs twice.** The completion seam enqueued a
  workflow advance whenever a step's agent *was* resting rather than when it
  *reached* rest, so every event a finished agent still emits — pending-message,
  MCP-status and tool-list updates — fired another one. Those ran concurrently
  and unlocked, each reading the run's cursor still on the step that had just
  finished, so each one entered the next step: several trace rows milliseconds
  apart (one "Running" that never finished, sitting above the "Done" row for the
  same step), and, worse, several *real* launches of the step's agent — which
  genuinely redid the work and genuinely spent the tokens. Advances are now
  triggered by the transition into rest, serialised per workflow, and each one
  claims the step's open trace before acting, so a duplicate is a no-op.

  This also **inflated reported run costs**: each closed trace adds its spend to
  the run total, and the duplicate rows carried identical deltas, so affected
  runs over-reported by roughly 40%. A migration supersedes the duplicated rows
  and rebuilds every run's totals from what is left — **existing runs will show
  lower (correct) token counts** after upgrading, and phantom "Running" rows
  disappear from their traces. Attempts abandoned this way are labelled
  `Superseded`. The repair takes more than closeness in time to act: a candidate
  must *also* be orphaned or an exact spend twin of the attempt that survived,
  because a legitimate `on_error=retry` whose first attempt fails within seconds
  starts inside the same window, and superseding that would delete real spend —
  the very under-reporting this repair exists to fix. Genuine repeat attempts
  (gate loop-backs, `on_error=retry`, manual retries, permission resumes) are
  untouched.

- **A failed turn dispatch tells its workflow.** `_fail_turn` marks the agent
  `failed` outside the event seam, so the run only found out if some later event
  happened to arrive — otherwise it sat in `running` until the watchdog (if one
  was configured at all) noticed. It now advances the run itself.

- **Answering a permission request no longer wedges the agent.** `needs_approval`
  is a *sticky* status — the idle handler skips it so a trailing idle can't mask
  a genuinely parked agent — so an agent left sitting in it never reaches
  `_on_idle`: its turn finished, the workflow was never told, and the step showed
  "Running" until the watchdog killed it. The reset to `running` now lives in
  `AgentManager.resolve_permission`, where every caller gets it, rather than only
  in the agents router (which is why approving from the workflow board silently
  stalled the run).

- **Re-driving a parked agent no longer queues behind the thing that parked it.**
  A turn stopped at a permission gate stays *open*, so sending the next prompt
  into that session just queued it behind a decision nobody was going to make —
  the "retry" burned its whole watchdog window without running anything and died
  with the same stall it was meant to fix. Starting a task now rejects any
  unanswered permission request and aborts the old turn first, so the new prompt
  lands on an idle session.

- **Live narration reached the workflow board.** `WorkflowAgentSummary` declared
  `active_narration` but the router returned ORM rows straight to FastAPI, so the
  field — which only exists in the runtime's in-memory view — was always null.

- **"Open Settings" from the Agents-off screen now lands on Agents.** The button
  told you to turn the feature on and then dropped you on Appearance, leaving you
  to find the right category yourself. It opens Settings directly on **Agents**,
  where the toggle it just pointed at is. The Workflows section's gated state got
  the same treatment: it used to name the setting in prose ("Enable it in
  Settings → Agents") without offering a way to get there, and now carries the
  same button.

- **Switching a step's kind or agent source no longer strands its agent.**
  Converting an inline step to an agent-backed one kept the reference to its
  private vessel, which the save then swept as an orphan — leaving a step reading
  "Missing agent". The reference is now dropped whenever the *source* changes,
  and kept when it doesn't, so an unchanged inline step still updates its vessel
  in place and keeps its run history.

- **Step colours now reset when a new run starts.** The strip derived each step's
  state from its *agent's* live status, which survives between runs — so a step
  that finished last time rests at `idle` and lit up green the instant a fresh
  run began, before it had done anything. Step state now comes from the **run
  trace** (what happened to that step *in this run*); a step with no trace yet
  reads `pending`. Scrolling the run picker replays how the strip looked for that
  run, while a live run never paints from a different one. The agent-status
  fallback (used by the gallery, which doesn't load traces) additionally refuses
  to mark anything past the running cursor as done.

- **An approval note now reaches the steps after it.** Approval checkpoints are
  transparent to the data flow (they publish no content, so the *material* still
  comes from the last real producer) — but that also silently dropped the
  reviewer's note, so approving with "translate it into French before sending"
  had no effect on the sending step. Notes are now read back off the run's
  approval traces and injected into every later step as a reviewer directive,
  alongside the content rather than instead of it. Approving with no note injects
  nothing.

- **The agents board uses the app's own tooltips.** Its hover hints were native
  browser `title` attributes, so they appeared after a long OS delay in a system
  bubble that ignored the theme — visibly different from every other hint in the
  app. The KPI tiles, the Auto badge, the narration line, the inbox rows and the
  new workflow chip all use the shared tooltip now.

- **Removed the unreachable agents sidebar list.** The agents section has been a
  card board since the dashboard landed, and the sidebar is force-collapsed (and
  un-expandable) in that mode — so the old `AgentList` sidebar rows could not be
  reached by any route, while still being maintained alongside every agent
  change. Deleted, along with the `agentSlot` plumbing that fed it.

- **No more double border on the in-progress step.** The active step drew a
  border *and* a 2px ring underneath the animated holographic frame; the frame
  now owns the edge alone. An approval step also no longer mislabels itself
  "Missing agent" — it legitimately has no agent, and reads "Human approval".

### Added

- **Workflows can be archived.** Archive hides a workflow from the gallery while
  keeping its definition and run history; the shared **Archive** panel gains a
  Workflows tab to restore or permanently delete it, alongside topics, chats,
  agents and live sessions. The backing endpoints existed but had no UI at all.
  Backed by a new `GET /api/workflows/archived`.

- **An agent shows which workflows use it.** Every agent **card on the agents
  board** carries a workflow chip with the number of pipelines using it. Clicking
  it jumps straight to the workflow when there's only one, and names them on the
  card when there are several — so the link is one click from the board rather
  than buried in settings. The agent settings panel keeps the same count and
  list. Editing a shared agent isn't a blind change any more. Archived workflows
  don't count, and private inline vessels never appear. Backed by
  `GET /api/agents/{id}/workflows` and a `workflow_count` on the agent payload
  (one grouped query, not one request per card).

- **Workflow schedules match topic and agent schedules.** The workflow recurrence
  editor now uses the same control as scheduled topics and agents: an arbitrary
  "every *N* minutes / hours / days" interval, or a **time of day on chosen
  weekdays**, in your local timezone. It previously offered four fixed presets
  (hourly / 6h / daily / weekly) with no weekday selection.

- **A real icon picker for workflows.** Browse a categorised set, search it by
  keyword, or paste **any** emoji (including from the OS picker) — and **No icon**
  is a first-class choice rather than a fallback gear. Clearing one also required
  a backend fix: the update handler tested `if payload.icon is not None`, so a
  null could never remove an icon once set.

- **Settings → Workflows.** A section for the defaults a new workflow and its
  steps start from: whether a step may use **tools**, **skills** and **memory**,
  and the **stall watchdog** a new workflow carries. All still overridable per
  workflow/step. A default of *on* leaves a step's override unset (inherit its
  agent); a default of *off* is written explicitly, because "inherit" would
  quietly turn it back on.

- **Workflows on the home page.** The launcher grid now offers Workflows
  alongside Topics, Chats, Live, Agents and Files.

- **Any prompt written inside a step stays private to it — including a gate's.**
  A step's agent is a hidden vessel whenever its prompt was authored in the step,
  so a one-off quality check ("is *this* joke safe for kids?") no longer leaves a
  permanent agent behind any more than a one-off task does. The choice is now
  **Existing agent** (reuse one from the Agents section) vs **Inline prompt**
  (write it here), offered for both Agent and Gate steps; the old "New agent"
  silently created a reusable agent instead. Referenced agents are never touched.

- **Inline steps — one-off work that isn't an agent.** A step can now be
  **Inline**: its instructions live with the step instead of in a reusable agent,
  so a pipeline stops leaving a trail of single-purpose agents in your Agents
  list, and the work is deleted along with the step (or the workflow). In a run
  it behaves exactly like a Task step — it produces content, hands it downstream,
  and takes the same context, capability and failure settings. Under the hood the
  step keeps a private hidden agent as its execution vessel (the runtime is
  agent-keyed); editing the step updates that vessel in place so its run history
  survives, and it's swept when no step owns it. It stays listed in
  **needs-attention** by design, so an inline step blocked on a tool approval
  can't wedge a workflow invisibly. Backed by `WorkflowStep.kind = "inline"` and
  `AgentSession.inline` (migration `b6e1f70a3c48`).

- **Steps are authored on the workflow board, not in a dialog.** Hit **Edit
  steps** and the horizontal strip you already know becomes editable — same
  layout, now authorable. **Drag a card** anywhere to reorder, **click a card** for that step's own
  settings modal (kind, agent — existing or created inline — instructions,
  context sourcing, capability toggles, failure policy), and the **+ between
  cards** inserts a step at that position, drawing the seam it will be spliced
  into as you hover. Keeping the horizontal layout is
  deliberate: the strip is the picture of the pipeline and shouldn't change shape
  just because you're editing it. The **New/Edit workflow** dialog keeps only the
  workflow's identity and run-wide settings, and a new workflow starts empty for
  the board to fill in. Editing is an explicit mode with an explicit save and is
  **refused while a run is in flight**: saving replaces the whole step list
  server-side and the run cursor is `ON DELETE SET NULL`, so a mid-run write
  would strand the coordinator. Backed by a new `WorkflowStepEditModal` that owns
  the draft model (`draftsFromWorkflow` / `draftsToPayload`), leaving
  `WorkflowBuilder` a third of its former size.

- **Workflow notifications.** A background pipeline can now reach you: a run
  raises a notification when it **finishes**, **fails**, or — most importantly —
  **parks on a human approval**, which is shown even when the app is focused
  because the run is *blocked* until you answer. Step-to-step progress stays
  silent and each transition notifies once. The `workflow.changed` SSE event now
  carries the run status and workflow name (the event bus whitelists payload
  keys and had been dropping `workflow_id` entirely).

- **Per-step context sourcing.** A step no longer has to inherit everything: pick
  **Previous step** (the default hand-off plus the artifact board), **Pick steps**
  to name exactly which earlier steps feed it, or **None** to run on its own
  objective alone. The run brief, reviewer directives and step instructions are
  always delivered — this governs the *material*, not the intent. Backed by
  `WorkflowStep.context_mode` / `context_sources`.

- **Per-step capability toggles (tools, skills, memory).** Agents gain
  `use_mcp` / `use_skills` / `use_memory`, and each workflow step can override
  them tri-state (auto / on / off). Tool schemas are a large fixed context cost
  paid every turn, so a step that only rewrites a paragraph needn't carry the
  whole MCP catalogue; a pure transform step is usually better off not consulting
  long-term memory. Flipping one rebuilds the agent's session on its next run.

- **A workflow-wide Assistant role.** Pick a role on the workflow and every
  step's agent adopts it for the run — applied at launch rather than stamped onto
  the shared agent rows, so the same agent can be formal in one pipeline and
  blunt in another. Backed by `Workflow.role_id` (migration `a9d4e7f21c60`).

- **Editing a step's agent without leaving the workflow.** The step modal now
  edits the agent's objective in place and toggles its capabilities, instead of
  bouncing out to the Agents cockpit to change one line. Saving primes the change
  for the step's next run; it never launches a turn.


- **Human approval checkpoints, with a reject policy.** A step can now be an
  **approval**: the run *parks* on it — no agent runs, nothing is spent — until a
  human decides. Where a gate is an agent judging the work, this is you judging
  it, so you can put a checkpoint in front of anything irreversible (sending the
  email, publishing, filing). Approving resumes the pipeline — and the note you
  leave is **forwarded to every later step** as a reviewer directive, so you can
  course-correct mid-run ("translate it into French before sending") without
  editing the workflow. Rejecting follows the checkpoint's **reject policy** — **send back** (loop an earlier step with
  your feedback injected, reusing the gate machinery and `max loops` cap),
  **stop the run** (recorded as `cancelled`, not `failed` — nothing broke, you
  decided), or **skip ahead**. The decision panel always also offers *Stop the
  run*, so the policy is a default rather than a cage. Backed by the `approval`
  step kind, `WorkflowStep.on_reject` (migration `f3b8d1c05a92`), an
  `awaiting_approval` workflow/run status, and
  `POST /api/workflows/{id}/approve` \| `/reject`. See
  [features/workflows](website/features/workflows.md).

- **Per-step instructions — one agent, a different job in each workflow.** A step
  can carry its own mandate, layered on the referenced agent's standing objective
  and taking precedence where they differ, so a single `Summariser` row can be
  the terse-bullets stage in one pipeline and the exec-brief stage in another
  without cloning it. Instructions land at the end of the kickoff preamble where
  they carry the most weight; on an approval step they're what the reviewer sees.

- **Per-step failure policy + stall watchdog.** A failed step no longer always
  kills the run: each step chooses **stop run** (the default), **retry** up to
  *N* times (with the failure reason injected so the retry isn't a blind repeat,
  each attempt appending its own trace row), or **carry on** for steps whose
  output is optional. Retry budgets reset per run. Separately, a workflow can set
  a **stall watchdog** (minutes; `0` = off): a step running past it is declared
  stuck, its agent cancelled, and it's put through that same failure policy — so
  an unattended pipeline can't sit in `running` forever behind a hung turn.
  Backed by `WorkflowStep.on_error`/`max_retries`/`retry_count`,
  `Workflow.step_timeout_seconds` (migration `e7c2f4a9b8d1`) and a scheduler
  sweep.

- **Cost roll-up per step and per run.** Every step attempt records the token
  spend of its turn and the run accumulates the total, shown in the run header
  and on each trace row — so a gate that looped four times reveals what each pass
  cost. Turns "did it work?" into "was it worth it?".

- **Per-run brief — point one reusable workflow at a different subject each
  run.** Starting a workflow now takes an optional free-text **brief** ("analyse
  `/data/q3-sales.csv`, EMEA only"), so the definition stays generic
  (`analyse → review → report`) while the run carries the subject. The **Run**
  button gains a caret that opens a brief composer (`⌘↵` to run); leave it empty
  and the pipeline runs fully autonomously exactly as before. The brief leads
  **every** step's kickoff preamble — not just the first — so a gate can judge
  the work against what was actually asked, a loop-back re-drives the producer
  with the brief still attached, and a final step knows which file it was about.
  It's stored on the run (new `workflow_runs.input` column, migration
  `d5e9b0c1a2f3`) and shown on the run header, so browsing back through history
  explains itself. `POST /api/workflows/{id}/run` accepts an optional
  `{ "input": … }` body and a webhook's posted payload becomes the run's brief;
  scheduled runs stay brief-free by design. See
  [features/workflows](website/features/workflows.md).

- **Workflow run history + inspectable per-step trace.** Every workflow
  execution is now persisted as a `WorkflowRun` with an append-only
  `WorkflowRunStep` trace — one row per step **attempt**, capturing what the step
  *received* (`input_context`) and *produced* (`output_summary`), plus gate
  verdicts and per-attempt duration. Gate loop-backs append a fresh attempt row
  rather than overwriting the prior one, so retries read as distinct entries.
  The workflow detail page gains a **run-progress header** (status, trigger,
  live current step, elapsed, % complete), a **run picker** to scroll back
  through past executions, and a collapsible **Run trace** timeline; the step
  modal now shows the **Input received** by a step and its full **Run history**
  across attempts. Backed by new `workflow_runs` / `workflow_run_steps` tables
  (migration `c3f7a1b2d4e8`), engine trace-recording in
  `services/agents/workflow.py`, and `GET /api/workflows/{id}/runs`. See
  [features/workflows](website/features/workflows.md). The run **result panel**
  now renders Markdown (bold, lists, rules) instead of raw text, the running
  step wears an animated holographic border mirroring the composer's
  thinking-state, and the run-trace rail is aligned dead-centre through its
  step dots. Runs are **deep-linkable** — `/workflows/<id>/run/<n>` pins a
  specific run and `/workflows/<id>/run/latest` follows the live one, with the
  URL updating as you scroll the run picker and staying put on a pinned run when
  new runs fire. Turns an agent runs **as a workflow step** no longer mark it
  **unread** in the Agents section — that badge is reserved for genuinely
  autonomous runs (started manually or by schedule), so the coordinator driving a
  step doesn't spam agent badges.

- **Workflow steps read the whole run's artifacts (shared blackboard).** Beyond
  the immediate hand-off (the previous step's full answer + artifacts), a step
  now also inherits the **artifacts published by every earlier step** in the
  run, labelled by step and oldest-first, as a reference-material section in its
  kickoff preamble. A reviewer three stages down sees the first step's research
  inventory without the middle steps having to re-forward it; steps that
  published nothing are skipped and gates never appear on the board. Backed by
  `collect_prior_artifacts` / `_earlier_content_agent_ids` in
  `services/agents/workflow.py`. See
  [features/workflows](website/features/workflows.md).

- **Workflow gates with conditional loop-back — simple prompt chaining with a
  quality gate.** A workflow step can now be a **gate**: it votes `PASS`/`FAIL`
  on the work so far and, on failure, re-drives an earlier step (its **on-fail
  target**) with the critique injected, so a bare chain like *tell a story → is
  it kid-safe? → note the story* loops until it passes — no special prompting
  needed. A step now feeds the next its **full answer** (not just the folded
  `OBJECTIVE_COMPLETE` summary), so results are shared implicitly. A gate is
  **transparent to the data flow** — the step after it receives the last real
  producer's output (the material the gate validated), not the gate's terse
  `PASS: …` verdict, so *tell a joke → is it kid-safe? → note the joke* actually
  notes the joke. Retries are
  bounded by a workflow-level **max loops** cap (default 3); exceeding it stops
  the run in `failed`. Backed by new `WorkflowStep.kind`/`on_fail_position`/
  `attempt_count` and `Workflow.max_loops` columns (migration
  `a7c93e21f4d8`), gate verdict parsing in `services/agents/workflow.py`, and
  Task/Gate controls in the workflow builder. See
  [features/workflows](website/features/workflows.md).
  **Workflows** mode (opt-in, off by default) turns a named sequence of
  independent agents into a `research → draft → review` pipeline where the
  *workflow* owns the chaining, not agent-to-agent `depends-on` links. Build one
  from existing agents or create steps inline; run/pause/resume/cancel it from a
  lifecycle bar; drill into any step's agent run in a modal while the pipeline
  stays live in the background. Each step is fed **only** the previous step's
  summary + artifacts, and a re-run clears each step's artifacts first. A workflow
  can be **parked** and triggered manually, on a **schedule** (hourly/6h/daily/
  weekly), or by **webhook** (`POST /api/workflows/hooks/{token}`). Backed by new
  `Workflow`/`WorkflowStep` models, a `/api/workflows` router, the
  `services/agents/workflow.py` coordinator, and a `workflow.changed` SSE event.
  See [features/workflows](website/features/workflows.md).
- **Park an agent for a trigger, then start it on demand.** The composer now has
  a **Create parked (don't run yet)** toggle: leave it on to launch immediately
  (the old behaviour), or turn it off to arm the agent in a new **`waiting`**
  lifecycle state. A parked agent stays idle until something triggers it — its
  webhook firing, the new **Start now** button on its detail view, or a workflow
  step reaching it. A parked agent is never auto-swept; only an explicit trigger
  launches it. Backed by a `start` flag on
  `POST /api/agents` and a new `POST /api/agents/{id}/start` endpoint. See
  `_spawn_agent`/`start_agent` in `routers/agents.py`.
- **Re-running an agent — or clearing its context — starts from a clean
  blackboard.** A fresh objective run (manual restart, retry, or webhook
  re-trigger) **and** clearing an agent's
  context with `/clear` now wipe the artifacts a previous run published, so the
  new turn's deliverables replace the stale ones instead of piling up beside
  them. Conversational follow-ups (`/send`) keep their artifacts. See
  `_clear_artifacts` (called from `start_task` and `clear_session`) in
  `manager.py`.
- **Multi-line ARTIFACT block form so deliverables land whole.** Substantial
  outputs (a research inventory, a draft, a review) can now be published as a
  block — `ARTIFACT: <title>` with no pipe, then the full Markdown body on the
  following lines, closed by an `END_ARTIFACT` line (or the next directive / end
  of message). This fixes the case where a model put its real deliverable across
  many lines and only a heading after the inline `|`, so the artifact captured
  just the heading. The single-line `ARTIFACT: <title> | <body>` form still works
  for short values, and a trailing `PROGRESS`/`OBJECTIVE_COMPLETE` line a model
  glued onto the body is now stripped off. See `_extract_artifacts` in
  `manager.py`.
- **Autonomy cadence promoted to the durable system layer.** The behavioral
  cadence that makes the dashboard useful is now baked into the autonomous
  agent's system preamble (`_AUTONOMY_PROTOCOL`) and per-turn nudge, so it
  applies to *every* run rather than relying on each mission prompt to restate
  it: narrate one short plain sentence before each action (feeding the live
  dashboard narration), emit `PROGRESS` several times across the run (early /
  middle / late, not only at the end), publish an `ARTIFACT` as each phase or
  finding lands, and close with a **2–3 sentence** `OBJECTIVE_COMPLETE` (up from
  a one-liner — it now reads better folded into the answer bubble). Task-specific
  specifics (named phases, exact checkpoint percentages like 15/40/65/90%) stay
  in the mission prompt: the durable layer sets the floor, the task sets the
  shape.
- **Artifact deliverables render as real Markdown, not run-on lines.** A single
  `ARTIFACT:` directive is one physical line, so a model can't press Enter inside
  it — a numbered list or multi-paragraph payload used to collapse into one
  inline paragraph. The autonomy prompt now tells the model to write `<content>`
  as Markdown using `\n` for line breaks, the directive parser unescapes those
  `\n`/`\t` sequences, and — as a safety net — a numbered list packed onto one
  line (`1. … 2. … 3. …`) is broken back onto separate lines (only a strictly
  sequential run, so decimals/versions/prices are left alone). The frontend
  mirrors the same normalization at render time, so **already-persisted**
  artifacts also render correctly without a re-run.
- **Objective-complete folded into the answer bubble.** A terminal
  `OBJECTIVE_COMPLETE` turn no longer renders as a separate emerald milestone
  node beneath an otherwise-hollow answer bubble. The completion is now folded
  **into the final answer bubble itself** — an *Objective complete* badge on the
  bubble, and (when the turn's prose was entirely control directives) the summary
  becomes that bubble's body — so the completion and the answer are one and the
  same, not two adjacent nodes echoing each other. Progress heartbeats still drop
  their own violet milestone pills on the spine.
- **Deliverables inline in the discussion flow — as a single, non-duplicated
  answer.** An agent's published artifacts now render at the foot of the
  conversation **unboxed, in the normal discussion background**: a horizontal
  rule slips each one off from the streamed prose, a quiet title row labels it,
  then the body renders as plain **Markdown** (Markdown passes through, JSON in a
  fenced block, `link` as a real anchor), so it reads as the agent's answer to
  your request. Copy content / copy link / open raw surface on hover. To stop the
  same content repeating as prose, completion, *and* deliverable, the `ARTIFACT:`
  payload no longer renders inline in the message body, and the section prefers
  the model's explicit `ARTIFACT:` outputs — falling back to the auto-captured
  completion summary only when nothing explicit was published (that summary is
  already shown in the *Objective complete* answer bubble). The insights-sidebar
  list stays as a compact index a workflow can feed into a later step's kickoff
  context — so the deliverable is consumable in the discussion *and* still travels
  the blackboard.
- **Addressable agent artifacts (permalinks + raw endpoint).** Published
  blackboard artifacts are now openable on their own. The insights-sidebar list
  entries are clickable: they open a viewer that renders the artifact by kind
  (Markdown, pretty-printed JSON, plain text, or a clickable link) with **Copy
  content**, **Copy link**, and **Open raw** actions. Two new routes back this —
  `GET /api/agents/{id}/artifacts/{artifactId}` (single artifact) and
  `GET /api/agents/{id}/artifacts/{artifactId}/raw` (raw body with a
  kind-appropriate content-type; a `link` artifact `307`-redirects to its URL).
  The permalink form `/agents/{id}?artifact={artifactId}` reopens the agent with
  the artifact auto-expanded.
- **Live agent narration in the dashboard.** While an agent has a turn in
  flight, `live_activity` now distils its own natural-language commentary (the
  first meaningful prose line of the streaming assistant message, stripped of
  control directives and markdown) into `AgentSessionRead.active_narration`. The
  dashboard card renders it under the active-tool chip (or as a standalone chip
  when no tool is running) and the sidebar rail surfaces it as the row label +
  tooltip, so a backgrounded agent reads as "what it's doing now" in plain
  language rather than a bare tool name. Mirrors to the frontend types.
- **A multi-agent orchestrator (budgets, triggers, blueprints, blackboard,
  unified inbox).** Agents mode graduates from independent runs to a coordinated
  **fleet** you watch from one dashboard. A **concurrency
  governor** (`agents_max_concurrent`, default 3) caps how many run at once; a
  per-agent **token budget** parks an agent in the inbox for approval instead of
  overspending; and **retry/backoff** (`max_retries` + `agents_retry_backoff_seconds`)
  auto-recovers a failed run with exponential delay before giving up. **Blueprints**
  (`/api/agents/blueprints`) save a reusable task + governance profile you stamp
  into fresh agents; a **shared-artifact blackboard** (`/api/agents/{id}/artifacts`)
  lets completed runs publish results (deduped) for a workflow or a watcher to read;
  **webhook triggers** (`/api/agents/{id}/triggers` + `POST /api/agents/hooks/{token}`)
  re-run an agent from an external event. Two new aggregate surfaces back the
  cockpit: `GET /api/agents/metrics` (fleet rollup — counts, running/queued,
  token + budget totals) and `GET /api/agents/inbox` (everything waiting on you —
  approvals, raised questions, budget parks — in one list). The dashboard grows a
  **unified inbox strip** and a **concurrency/token rollup**; each run's insights
  sidebar gains an **artifacts panel** and **webhook**
  management; the agent settings drawer gains **token budget** + **max retries**;
  and Settings gains a **blueprints** manager. New `AgentSession` columns
  (`token_budget`, `max_retries`, `retry_count`, `retry_at`, `total_input_tokens`,
  `total_output_tokens`) and three new tables (`agent_triggers`,
  `agent_artifacts`, `agent_blueprints`) land in one Alembic
  migration; all new read/create schemas mirror to the frontend types.
- **Autonomous agent missions (opt-in).** An agent can now be started in
  **autonomous** mode, turning its task prompt into a **durable objective** it
  pursues on its own: a **goal loop** inspects each idle turn for a control
  directive and either finishes, blocks for input, or **continues itself** to the
  next step, so a multi-step mission lands as a **single** result in the linked
  topic/chat instead of a burst of intermediate turns. Agents self-report
  **progress** (`PROGRESS: <0-100> | label` → a progress bar on the card, the
  mission strip, and the open run), raise a question via `NEED_INPUT:` to enter a
  new **Needs input** (`blocked`) state that floats to the top of the dashboard
  next to approvals (your reply resumes the mission), and finish with
  `OBJECTIVE_COMPLETE: <summary>`. A per-agent **step budget** (default 12) plus a
  **stall guard** cap the loop so it can't spin forever; a blocked agent's
  scheduled re-runs are skipped so a cadence never discards its question. The mode
  is **off by default** — plain agents rest at Idle after each turn exactly as
  before — and is toggled (with a step-budget input) on the start-agent form. New
  persisted fields on `AgentSessionRead` (`autonomy_enabled`, `max_steps`,
  `step_count`, `progress`, `progress_label`, `blocked_question`) mirror to the
  frontend types.
- **Per-agent approval policy.** The approval policy gating an agent's actions is
  now **selectable per agent** instead of only globally: choose *Inherit global
  default* / `manual` / `balanced` / `autonomous` on the start-agent form or from
  the agent's settings drawer. A nullable `approval_policy` column on
  `AgentSession` (`NULL` = inherit) is resolved **per turn**, so switching it
  needs no session rebuild, and it wins over the Settings-wide default only when
  set. Exposed on `AgentSessionRead` / `AgentSessionCreate` / `AgentUpdateRequest`
  and mirrored to the frontend types.
- **An agent control-tower dashboard.** Agents mode now opens on a fleet
  **dashboard** instead of a single run: a row of **KPI stat tiles** (**Need
  you** / **Working** / **Idle · done** / **Scheduled**) — the *Need you* tile
  glowing amber and the *Working* tile spinning while live — over **urgency
  swimlanes** of monitor cards, one per agent, each with a live status medallion,
  current tool, next scheduled run, unread count, linked topic/chat, and model.
  Cards are **urgency-sorted** by an attention router — an agent blocked on you
  floats to the top, then interrupted/failed, then live work, then quiet states —
  and grouped into **Needs you / Working / Idle · done** lanes with a hover-lift
  and a status-colored rail. A shared **status medallion** (pulses while working,
  wears an amber ring when it needs you, grows a fan-out cluster badge for
  parallel tool calls) renders identically on the dashboard and the command
  palette. While an agent works, its **current tool** and a `×N parallel` count
  are shown live, derived from a new in-process `live_activity()` snapshot exposed
  via three fields on `AgentSessionRead` (`active_tool`, `active_tool_count`,
  `pending_permission`).
- **An out-of-band "agent needs you" signal.** When a background or scheduled
  agent pauses for approval, a browser notification now fires **regardless of
  window focus** and deep-links to that agent, the **tab title** grows a 🔔 and a
  waiting count, and the **⌘K palette** lists the agents that need attention
  first — so you can leave agents running in the background and be pulled back
  only when one is genuinely blocked. All of it respects the existing
  notifications toggle.
- **A lapsed idle MCP sign-in surfaces itself, instead of stalling your next
  request.** WorkIQ / [Agent 365](website/features/mcp.md#agent-365-workiq-teams-and-workiq-user)
  credentials that had gone idle (per the keep-alive back-off) used to die
  quietly — you'd only find out when a simple request stalled for seconds on a
  doomed OAuth handshake before the sign-in banner finally appeared, or by
  manually opening **Settings → MCP**. Now the keep-alive **probes an idle token
  once it has genuinely expired** and raises the `McpAuthBanner` proactively if
  its refresh token is dead (a still-refreshable session recovers silently), and
  a detected lapse records a verdict so the **first turn** that touches the
  server **fast-fails straight to the prompt** rather than paying the multi-second
  connect. New `workiq_keepalive_surface_idle_lapse` knob (default `true`,
  `PRECURSOR_WORKIQ_KEEPALIVE_SURFACE_IDLE_LAPSE`) opts back into fully silent
  idle credentials.
- **Per-collection default role.** A [collection](website/features/collections.md)
  can now nominate a default **Assistant Role** — new topics created in it start
  with that persona unless the caller picks another one, so a whole collection
  can lean into one role without setting it per topic. Set it from **Settings →
  Collections**; a new nullable `default_role_id` on the collection (schema, API,
  and a migration) drives it, and deleting a role reverts any collection that
  pointed at it back to the built-in default.
- **Copilot AI-credit usage in the persona menu.** When you're connected with a
  GitHub account, opening the sidebar persona menu now shows your Copilot **AI
  credits** — a progress bar with the percentage used and the next reset date.
  It's fed by a new `GET /api/me/copilot` endpoint that reads the account's
  `premium_interactions` quota from GitHub with your own token and is fetched
  lazily on menu open (so `/api/me` stays fast). The bar warms amber past 75%
  and red past 90%; unlimited plans show an **Unlimited** badge, and accounts
  with no Copilot seat simply omit the section.
- **Choose the Playwright browser from Settings.** The MCP tab now has a
  **Playwright browser** selector (`msedge` by default, plus `chromium`, `chrome`,
  `firefox`, `webkit`, and `Default`) backed by a DB-overridable
  `playwright_browser` setting that wins over the `PRECURSOR_PLAYWRIGHT_BROWSER`
  env default and applies live — the warm worker is retired so the next call
  relaunches with the new channel. The new **Default** choice **omits `--browser`
  entirely**, the escape hatch for environments whose resolved `@playwright/mcp`
  predates the flag and fails to start with `error: unknown option '--browser'`.
- **Playwright `--browser` support is now auto-detected.** On startup Precursor
  probes the resolved `@playwright/mcp` once and **omits `--browser` automatically**
  when the build predates the flag (e.g. behind a stale registry mirror), so the
  server launches instead of failing with `error: unknown option '--browser'` — no
  configuration needed. The **Default** selector remains as an explicit override.
- **Kanban issue comments now show when they were posted.** The issue/PR
  preview modal renders each comment's timestamp (localized medium date + short
  time) next to the author, with an *(edited)* hint when the comment was updated
  after posting. The GitHub `IssueComment` schema and its TypeScript mirror now
  carry `created_at` alongside `updated_at`.

### Changed

- **An approval step's run detail reads like any other step's.** Opening one used
  to show a red error — "no agent runs for it" — and nothing else, hiding the
  brief, the input and the decision that *were* recorded. It now shows the same
  anatomy as every other kind: what the reviewer was asked, the **Decision**
  (verdict + note), the input it received and the full attempt history, with the
  no-agent fact stated as a neutral note rather than a failure.
- **The run-step modal is read-only.** Opening a step from a run is for
  inspecting what happened, so it no longer offers objective editing, capability
  toggles or a "nudge this step" composer — authoring lives in *Edit steps* on
  the board, and a past run shouldn't be mutable from the record of it. Its
  **Open in agents** link (and the one in the run trace) now appears only for a
  *reusable* agent: an inline step's vessel isn't listed in the Agents section
  and an approval step has no agent at all. Backed by a new `agent_inline` flag
  on the run-step trace.
- **Removing a webhook lives on the webhook control**, not buried in the
  schedule editor — revoking a token has nothing to do with recurrence. It now
  confirms first, since the URL stops working immediately.
- **Step authoring reads more clearly.** The step modal asks for the **kind**
  first (**Agent** / Inline / Gate / Approval) and only then, for an Agent step,
  whether it reuses an **existing agent** or creates a **new** one — the old
  order asked how before what. "Task" is now **Agent**, since that is exactly
  what distinguishes it: it is backed by a real agent from the Agents section.
- **One prose field per step.** The separate step-instructions box now appears
  only where it adds something — a step reusing an *existing* agent, where it
  customises a prompt written elsewhere (and says so), plus an Approval step,
  where it is the only description. An Inline step, or an Agent step authored on
  the spot, already states its job in its own field.
- **Dragging a step shows where it will land**, as a vertical seam between two
  cards — the same signal the `+` uses — instead of highlighting whichever card
  sits under the cursor, which read as "replace this one".
- The workflow settings dialog no longer carries a note pointing at *Edit steps*,
  and the **Expand sidebar** control is hidden on the Workflows page as it
  already is on Agents.

### Fixed

- **A workflow step no longer stalls asking for clarification.** A task step
  whose objective was slightly under-specified (e.g. *"note this joke 0–10"*)
  could emit `NEED_INPUT` / present a menu of options instead of acting — which
  parked the step **Blocked** and paused the whole run, since a workflow runs
  unattended with no human to answer. Every non-gate step's kickoff now carries a
  strict autonomy directive: complete the objective directly on the given input,
  never ask for clarification or emit `NEED_INPUT`, and if a detail is
  underspecified pick the most reasonable interpretation and produce the
  deliverable anyway. Backed by `_TASK_PREAMBLE` in
  `services/agents/workflow.py`. See
  [features/workflows](website/features/workflows.md).

- **A completed step now shows its deliverable, not the terse completion
  reason.** When an autonomous step wrote its output as prose and then ended with
  `OBJECTIVE_COMPLETE: <reason>` (a *meta* description like "Told the user a
  joke"), the step displayed — and auto-captured as its **"Result" artifact** —
  the reason instead of the actual deliverable (the joke), even though the
  downstream hand-off correctly received the real prose. The completion branch
  now prefers the message's prose body for the displayed `result_summary` and the
  Result artifact, falling back to the reason only when the final turn was
  directives-only (a bare `OBJECTIVE_COMPLETE` with no body). See
  [features/workflows](website/features/workflows.md).

- **Control directives no longer leak into a step's displayed result.** A gate
  stored its raw `OBJECTIVE_COMPLETE: PASS: …` verdict as the step result *and*
  auto-captured it as a shared **"Result" artifact**, so directive syntax showed
  up as a deliverable and polluted the blackboard. A gate is now normalised to a
  plain `Passed — <reason>` / `Rejected — <reason>` verdict and emits **no
  artifact** (a gate judges, it doesn't produce). More generally, control tokens
  (`OBJECTIVE_COMPLETE`, `ARTIFACT`, `PROGRESS`, `NEED_INPUT`, …) are now scrubbed
  from every step's *displayed* `result_summary`; the raw turn is still retained
  internally for directive parsing, content forwarding, and gate verdicts. See
  [features/workflows](website/features/workflows.md).


  lifecycle-directive parser matched `NEED_INPUT:` / `OBJECTIVE_COMPLETE:` /
  `PROGRESS:` **anywhere** in an assistant message, so an agent explaining "I emit
  **NEED_INPUT:** to the dashboard when blocked" tripped the marker mid-prose,
  captured the trailing `**` and surfaced a garbled phantom question (`** to your
  dashboard when blocked.`) that halted the run/workflow. Directives are now only
  recognised at the **start of a line**, and the closing `**` of a bolded
  `**LABEL:**` is eaten so it never leaks into the captured value. Mirrored in the
  backend parser (`parse_agent_directives` in `services/agents/manager.py`) and the
  frontend (`lib/directives.ts`).
- **A parked agent no longer hides the entire agent list.** The `waiting`
  lifecycle state (parked agents) was added to the DB and frontend but omitted
  from the `AgentSessionRead.status` literal, so one waiting agent raised a
  Pydantic `literal_error` for the whole `GET /api/agents` response — the list
  500'd and the UI fell back to the empty-state start wizard, making every agent
  look gone. `waiting` is now part of the `AgentStatus` schema literal (already
  present in the TS `AgentStatus` type). See `schemas/agent.py`.
- **The agent insights sidebar now refreshes live.** The per-agent orchestration
  panel (shared artifacts + triggers) fetched once when the agent was selected and
  never again, so mid-mission `ARTIFACT:` outputs and the completion result only
  appeared after a manual reload. It now subscribes to the `agent.changed` bus —
  mirroring the in-chat deliverables — so the right bar updates the moment new
  content is published.
- **Autonomous agents no longer randomly hit "Permission denied" on every tool
  call.** When an agent's detail page was open as the agent started, the timeline
  poll (`GET /api/agents/{id}/events`) and `start_task` could each call
  `_ensure_live` before either cached the session, firing **two** `session.create`
  requests for the same `copilot_session_id`. The duplicate create left the CLI's
  permission responder mis-wired, so every tool call — including built-in `rg`/shell
  — was denied non-interactively with *"Permission denied and could not request
  permission from user"*, even under the `autonomous` policy, and the run parked
  itself with a spurious `NEED_INPUT`. `_ensure_live` now serialises its
  check-then-create under a per-agent lock, so concurrent callers reuse the single
  session. No API or schema change.

### Changed For autonomous agents, the control-directive markers (`NEED_INPUT:`,
  `PROGRESS:`, `ARTIFACT:`, `OBJECTIVE_COMPLETE:`) are **stripped from the rendered
  message body** — they were previously persisted verbatim and left the one line a
  human must act on buried in prose. The raised question is re-surfaced as its own
  amber **"Needs your input"** callout inside the message (rendered as markdown,
  so emphasis shows), and both that callout and the top blocked banner gain an
  **Answer** button that scrolls to and focuses the reply composer. `PROGRESS:`
  heartbeats and the terminal `OBJECTIVE_COMPLETE:` are additionally re-drawn as
  compact, iconified **milestone nodes on the transcript spine** (violet
  percentage pills capped by an emerald *Objective complete* node), and
  `ARTIFACT:` publications render inline as teal **Published artifact** cards
  (collapsible, mirroring the sidebar blackboard), so a mission's trajectory and
  its named outputs read in the discussion instead of as raw directive lines.
  Pure frontend (`frontend/src/lib/directives.ts` mirrors the backend directive
  parser); no API or schema change.
- **Editing an agent saves without running it.** Changing an agent's objective,
  role, or governance in the settings drawer used to **auto-replay** the task the
  moment you saved. **Save** is now save-only — a changed objective/role *primes*
  the new instructions (the cached SDK session is dropped) but no turn is
  launched. A new **Save & run** button combines both: it persists the edits and
  then starts the objective (clearing the prior run's artifacts first), and is
  disabled while the agent is already active. `PATCH /api/agents/{id}` no longer
  enqueues `restart_with_task`; the frontend runs via the existing
  `POST /api/agents/{id}/start`.
- **Autonomous agents no longer emit user-facing "suggest" follow-ups.** The
  trailing follow-up-chip instruction is now injected only for **plain** agents
  (a human converses with them turn-by-turn); an **autonomous** agent drives
  itself through the control directives and runs unattended, so soliciting
  next-step suggestions there only spent tokens and pulled against both the
  "keep going, don't ask" autonomy contract and the base CLI prompt's
  "don't offer to continue" tone rule. Backend-only preamble tweak in
  `_system_preamble`; no API or schema change.
- **The autonomy protocol now explicitly outranks the base "end tersely" tone
  rule.** The captured GitHub Copilot CLI base prompt instructs the model to end
  a turn without a recap, summary, or status — which pulls directly against the
  control lines (`PROGRESS:` / `OBJECTIVE_COMPLETE:`) an autonomous agent must
  emit for the system to track its mission. `_AUTONOMY_PROTOCOL` now states that
  those control lines are required output and take precedence, reconciling the
  conflict and hardening directive reliability. Backend-only injection tweak; no
  API or schema change.
- **Agents mode drops the parallel session list for a dashboard-first
  navigation.** Opening Agents no longer shows a left list of agent sessions
  next to an open agent (which made agents feel like "alternative topics").
  The sidebar collapses to a **slim icon rail** used only to switch sections,
  and the **fleet dashboard is the home** for monitoring existing agents. Open a
  single agent and its header grows an **← All agents** back button (re-clicking
  the **Agents** rail icon does the same) that returns to the dashboard. Per-agent
  management — rename (double-click the title), **archive**, stop, and delete —
  now lives in the open agent's header, so no capability is lost with the list
  gone.

- **The GitHub Models provider is retired and no longer offered.** GitHub has
  retired the service; `https://models.github.ai/catalog/models` now answers
  `410 Gone`, so selecting the provider only ever produced a raw HTTP error
  where the model list should be. Providers can now carry a `retired` reason in
  the registry, which is the single source of truth for both the settings notice
  and the API: `GET /api/llm/providers` hides retired providers (unless one is
  still selected, in which case it is labelled *(retired)*), and
  `GET /api/llm/models` answers `502` with that explanation instead of leaking
  the upstream `410`. Existing configurations are **not** silently rewritten —
  the provider stays selectable-but-flagged so the switch to **GitHub Copilot**,
  which authenticates with the same GitHub token, is a deliberate choice.

- **Copilot's intermittent model refusals are ridden out instead of surfaced.**
  Only part of Copilot's fleet serves every model, so an identical request
  alternates between `200` and `400 model_not_available_for_integrator` at
  roughly a coin-flip — measured at 3/8 to 5/8 success for `MAI-Code-1-Flash`
  and `gemini-3.5-flash`. Despite the 4xx this says nothing durable about the
  model, so Precursor now retries the rejection while opening the stream (up to
  5 attempts, short backoff), which takes both models from a coin-flip to 6/6 in
  end-to-end runs. The retry is safe because the refusal happens *before* any
  token is streamed, and it is scoped to this one error code — every other 4xx
  still surfaces immediately. If it does keep failing, the message now says the
  rejection is intermittent and worth retrying rather than claiming the model is
  permanently out of reach.

- **`GET /api/reminders/{container}/{id}` returns `200` with a `null` body when
  no reminder is set**, instead of `404`. The conversation panel reads this
  endpoint on every topic/chat open, so the far more common "no reminder" case
  was logging a red `404 (Not Found)` in the browser console on nearly every
  navigation — even though the client already handled it. Unknown container
  kinds are still a genuine `404`.

- **Lockfiles are now resolved by CI, not by contributors**: many managed
  devices route uv and npm through a corporate package mirror, and re-resolving
  there doesn't merely relabel URLs — npm integrity comes back as `sha1` instead
  of `sha512`, and uv drops the `size`/`upload-time` provenance. Because that
  reads as an innocuous URL diff in review, it had already reached the
  repository: **601 dependencies were pinned with sha1**. All three lockfiles
  are regenerated from the public registries and now carry **only `sha512` and
  public URLs**. A new **`Relock`** workflow is the supported way to change a
  dependency (`gh workflow run relock.yml --ref <branch>`); it resolves on a
  clean runner, verifies the result installs *and* builds, then pushes back to
  the branch or opens a PR. A stdlib-only guard (`make lockcheck`, an opt-in
  pre-commit hook via `make hooks`, and a CI job) rejects proxy URLs and weak
  hashes, and `uv sync --locked` in CI turns a stale lock into a failure rather
  than a silent re-resolution. `make sync` now uses `npm ci`, and the Makefile
  exports `UV_FROZEN=1` — every `uv run` re-locks otherwise, so `make dev`,
  `make check`, and `make test` were each rewriting `uv.lock`.

- **MCP: one sign-in prompt instead of one per credential**: the WorkIQ preview
  and the Agent 365 servers are different Entra clients against different
  resources, so they hold separate tokens on separate expiry clocks — which used
  to mean a second banner and a second click every time both aged out. Pending
  sign-ins are now tracked **per credential** and collapsed into a **single
  banner** naming every server involved, with re-auth attempts serialized so two
  flows can't race for the same window. The one **Sign in** you click now also
  **chains the others**: as soon as it succeeds, the remaining credentials retry
  on the hands-free silent path while the Entra SSO session is hot (and the
  backend re-arms the prompt for servers parked on a different stale
  credential), so in the common case they renew with **zero extra clicks**. Set
  `workiq_chain_reauth_enabled=false` to renew each credential only when it's
  independently needed. Previously the second credential never even attempted the
  silent pass and always fell through to a manual click. See
  [MCP → One prompt, not one per credential](https://lrivallain.github.io/precursor/features/mcp.html#one-prompt-not-one-per-credential).

- **MCP: a busy loopback port no longer blocks a WorkIQ sign-in**: each
  OAuth-protected server still *prefers* its fixed callback port (`12798`,
  `12799`, `12800`), but when that port is taken — another Precursor window
  mid-sign-in, or an unrelated app — the flow now falls back to a free
  **ephemeral port** instead of failing fast. Entra ignores the port of a
  loopback redirect for public clients, so the fallback is transparent and
  several windows can sign in concurrently. Set
  `workiq_loopback_port_fallback=false` to restore the previous strict
  behaviour.

- **MCP: sign-in prompts are collapsed per credential on the backend too**: when
  a turn pauses because MCP servers need authenticating, the blocked list is now
  reduced to **one name per credential** before anything is announced, so two
  blocked Agent 365 servers ask you to sign in **once** rather than twice. This
  lands at a single choke point (`auth_blocked_servers`), so chat, topic,
  workspace and scheduled-command pauses all inherit it. Which servers share a
  credential is no longer hardcoded per call site: a new **OAuth server
  registry** is the one place that knows which built-ins sign in, what to call
  them, and which of them are backed by the same token.

- **MCP: keep-alive backs off for credentials you aren't using**: the ticker
  still refreshes a token shortly before it expires so an active session never
  breaks mid-turn, but a WorkIQ server enabled long ago and never called is now
  left alone — no refresh, and crucially **no sign-in prompt** when its refresh
  token finally lapses. Usage is tracked per credential (calling either Agent 365
  server keeps the shared token warm) with a **6 hour** default window, and the
  clock is seeded at process start so a restart doesn't leave everything cold.
  Set `workiq_keepalive_idle_after_seconds=0` to keep every signed-in credential
  warm indefinitely, as before. See
  [MCP → Quiet when you're not using it](https://lrivallain.github.io/precursor/features/mcp.html#quiet-when-you-re-not-using-it).

### Added

- **A Markdown formatting toolbar and keyboard shortcuts on the Markdown
  composers.** The editors that render their value as Markdown — live-session
  **notes** and **summary**, and the GitHub **issue / comment drafts**
  (`/gh-update`, `/gh-create`, and the comment box) — now carry a small
  formatting toolbar (bold, italic, strikethrough, inline code, link, heading,
  quote, and bulleted / numbered lists) that rewrites the current selection in
  place. The same actions are bound to shortcuts — <kbd>⌘/Ctrl</kbd> + <kbd>B</kbd>
  (bold), <kbd>I</kbd> (italic), <kbd>K</kbd> (link), <kbd>E</kbd> (code), and
  <kbd>⌘/Ctrl</kbd> + <kbd>⇧</kbd> + <kbd>X</kbd> (strikethrough). Toggles are
  reversible (re-apply to unwrap), an empty selection parks the caret between the
  markers, and <kbd>⌘/Ctrl</kbd> + <kbd>K</kbd> stays local to the field instead
  of bubbling to the global command palette. The plain-text fields (system
  prompts, memories, roles) are untouched.


  one collection, unread activity in the others was invisible. The switcher now
  carries a dot when another collection has unread messages, and each row in its
  dropdown shows that collection's own unread count.

- **The GPT-5.5/5.6, Codex, Grok and MAI models now actually work.** Copilot
  serves its catalogue across two API surfaces, and a model offered by one is
  rejected by the other — Precursor only ever spoke `/chat/completions`, so
  **eight of the models it listed** (`gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.5`,
  `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `grok-4.5` and
  `mai-code-1-flash-picker`) answered a raw `400` the moment you sent a message.
  Precursor now speaks the **Responses API** as well and routes each model to the
  surface that serves it, reading the `supported_endpoints` the catalogue already
  publishes. Streaming, tool calls, images and reasoning effort behave the same on
  both. Models the catalogue says we can drive with *neither* endpoint are no
  longer offered at all, since listing a model that cannot be called only
  guarantees a failure once it is picked.

- **Type-to-filter in the model pickers**: the composer and agent model menus
  now open with a search box focused, narrowing the list as you type. Matching a
  vendor heading (`anthropic`, `microsoft`, …) keeps that whole group, so the
  catalogue can be sliced by publisher as well as by model name, and <kbd>Enter</kbd>
  picks the first match. Menus with only a handful of options — reasoning effort,
  context size — stay plain lists.

- **MCP: console tracing for the WorkIQ sign-in legs.** The hands-free re-auth
  hides two sequential attempts inside one request — the invisible `prompt=none`
  iframe, then the backend self-opening the OS browser — so when the manual
  banner finally appears there was no way to tell which leg gave up, or why.
  Each step of an auth episode now logs to the browser console under
  `[workiq-auth]` with elapsed timings: when a notice opens, when the
  authorization URL arrives over SSE (published for the silent leg only, so its
  absence is itself diagnostic), how the hidden frame navigated — including
  whether it was refused before any document loaded, the signature of
  `X-Frame-Options` blocking the silent pass outright — and which outcome
  produced the banner. `window.precursorWorkiqAuthTrace()` returns the whole
  episode for pasting into a bug report; `login_hint`, `state` and `nonce` are
  never logged. Silence it with
  `localStorage['precursor.debug.workiqAuth'] = '0'`.

- **Collections**: split topics into separate workspaces of work. A switcher at
  the top of the Topics panel filters the sidebar tree (and the Pinned section)
  to one collection at a time; search, the command palette, and the archive stay
  unfiltered, and opening a topic from another collection switches to it. Move
  topics via the sidebar context menu, topic settings, or the new
  **`/collection <name>`** command — sub-topics always follow their parent.
  Each collection carries a name, description, colour accent, and an optional
  **GitHub repo** override, giving repo resolution a three-step chain:
  `topic.github_repo` → `collection.github_repo` → the global setting. Manage
  them in **Settings → Collections**; deleting one re-homes its topics to a
  destination you choose rather than deleting anything. Existing topics are
  backfilled into a protected **General** collection. The MCP `list_topics` /
  `get_topic` tools now report a topic's collection, and `list_topics` accepts a
  `collection` filter. See [collections](https://lrivallain.github.io/precursor/features/collections).

- **Search with `/`**: pressing <kbd>/</kbd> anywhere outside a text field now
  opens the command palette straight into search — the same launcher as
  <kbd>⌘K</kbd> / <kbd>Ctrl-K</kbd>, one key away. The shortcut stands down
  whenever you're typing (inputs, textareas, and rich contenteditable editors),
  so the composer's `/` slash-command picker is untouched, and it won't stack the
  palette on top of an open dialog.

- **Sidebar contextual menus**: right-click topic, chat, Live, and agent rows for
  the actions each surface supports. Topics and chats expose rename,
  read/unread, pin/unpin, reminder, notes, and archive; agents expose rename,
  read/unread, and archive; Live sessions expose rename and archive.

- **MCP: Microsoft Agent 365 servers (`workiq-teams`, `workiq-user`)**: two new
  built-in, OAuth-protected streamable-HTTP servers covering **Teams** (chats,
  channels, messages, members, presence) and the **directory** (profiles,
  managers, direct reports). They reuse the existing WorkIQ browser sign-in
  stack — silent-first re-auth, keep-alive refresh, inline auth banner — and
  **share a single sign-in**: they authenticate as the same Entra client against
  the same resource, so one token serves both (the WorkIQ preview keeps its own,
  separate one). Their endpoint URL
  embeds a Microsoft **tenant GUID**, resolved from **Settings → MCP →
  Microsoft 365 tenant**, then `PRECURSOR_WORKIQ_TENANT_ID`, then — as a
  convenience — the `tid` claim of a token from an existing WorkIQ sign-in, so
  in practice there is usually nothing to configure. See
  [MCP → Agent 365](https://lrivallain.github.io/precursor/features/mcp.html#agent-365-workiq-teams-and-workiq-user).

- **Installable app (PWA)**: Precursor now ships a web app manifest, PNG icons
  (incl. a maskable variant), and a minimal service worker, so Chromium browsers
  offer to **install it as a standalone app** (own window, dock/taskbar icon).
  The service worker registers only in the built SPA (a one-process
  `precursor` run), does **no offline caching**, and passes all traffic through
  to the network — it exists purely to meet the browser's installability bar.
  It stays a convenience wrapper around your local instance: works while the
  process is running, on the same machine over `localhost`. See
  [Installation → Install as a browser app](https://lrivallain.github.io/precursor/guide/installation.html).

- **MCP: cross-entity search + chats/agents/live accessors**: the built-in
  `precursor` MCP server's `search` tool now spans the same surfaces as the ⌘K
  palette — topics, chats, agents, and live (meeting) sessions — and each hit
  carries an `accessor` hint pointing to the tool that returns its full content.
  Three new opt-in `mcp_expose` sections back this: **`chats`** (`list_chats`,
  `get_chat`, `list_chat_messages`), **`agents`** (`list_agents`, `get_agent`),
  and **`live`** (`list_live_sessions`, `get_live_session` — notes, summary,
  transcript, and insights). Chat/agent/live search hits only surface when their
  section is exposed, so snippets never leak content the host hasn't opted into.

- **Playwright MCP server (authenticated scraping)**: a new built-in `playwright`
  tool server wraps Microsoft's official `@playwright/mcp` (launched via `npx`,
  like `workiq`) so agents, topics, and chats can drive a real browser —
  navigate, read the rendered DOM/text, and screenshot. It runs **headed** and
  defaults to **Microsoft Edge** (`--browser msedge`) so it can ride the
  corporate Edge SSO/WAM broker for authenticated Entra scraping, reusing
  `@playwright/mcp`'s **shared, machine-wide persistent profile** so a sign-in
  already onboarded there (including via other Playwright-MCP tools) carries over
  and reaches **authenticated** pages that `fetch` (raw HTTP, no browser/session)
  can't. `PRECURSOR_PLAYWRIGHT_BROWSER` (default `msedge`) picks the channel and
  `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` pins an isolated profile. Toggle it in
  **Settings → MCP**; a host-dependency preflight requires Node.js (`npx`) on
  PATH.

- **Attach text & code files**: the message composer now accepts plain-text and
  source files (`.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.toml`, `.xml`, `.py`,
  `.ts`, `.js`, `.go`, `.rs`, `.sql`, `.sh`, and many more) in addition to images
  and PDF/DOCX/PPTX. Their UTF-8 text is extracted and folded into the turn as
  context. Because browsers report inconsistent MIME types for source files,
  acceptance falls back to the file extension and the stored type is normalized
  to a `text/*` MIME.

- **Edit transcript phrases inline**: in a Live session, **double-click any
  phrase** in the transcript to correct misheard words in place — press
  <kbd>Enter</kbd> (or blur) to save, <kbd>Esc</kbd> to cancel. The correction
  persists to the session and feeds the insights and summary. Backed by a new
  `PATCH /api/live/{id}/segments/{segment_id}` endpoint.

- **Live transcript auto-cleanup**: a Live session's raw transcript is now
  automatically deleted a configurable number of days after the session ends
  (**7 by default**; `0` keeps it forever), bounding database growth. Only the
  transcript is removed — the session and its insights, notes and summary are
  preserved — and only *ended* sessions are eligible, so an active or paused
  recording is never touched. Configure it under **Settings → Live**
  (`live_transcript_retention_days`); the sweep runs on startup and daily via a
  background ticker.

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
  is enabled, the **Summary** tab gains a **Generate from Teams transcript**
  button. It scrapes the meeting's published transcript through WorkIQ (Microsoft
  Graph:
  `/me/onlineMeetings` → `/transcripts` → VTT `/content`) and generates
  Precursor's own structured recap — so you don't need to capture the meeting
  audio locally. Best-effort and fail-closed: it requires you to be the meeting
  organizer, the delegated `OnlineMeetingTranscript.Read.All` permission, and a
  transcript that Teams has already published (a few minutes after the meeting
  ends). The **Summary** tab is always available; its actions light up based on
  the data at hand — generate from the local recording and/or from the Teams
  transcript. New endpoint `POST /api/live/{id}/summary/from-transcript`; the
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

- **Hands-free WorkIQ sign-ins close their window instantly**: the loopback
  success page now closes immediately for the silent and self-triggered
  (`auto`) re-auth passes instead of showing the manual flow's two-second
  "Closing this tab in…" countdown. Nobody is watching an automatic sign-in, so
  it no longer leaves a stray window sitting around after it succeeds; the
  manual, banner-driven sign-in keeps its brief confirmation beat.

- **WorkIQ re-auth is now hands-free and self-triggering**: when the WorkIQ
  refresh token ages out, Precursor prefers automation over interrupting you. It
  runs the silent `prompt=none` pass in an invisible iframe first and, when that
  needs interaction, **self-opens your OS browser** to a single visible sign-in —
  no `McpAuthBanner` click and no redundant second prompt. The manual banner only
  surfaces as a last resort (auto re-auth off, loopback port busy, or declined).
  A new `auto=true` mode on `POST /api/mcp/servers/workiq/reauthenticate` drives
  this; the interactive fallback opens the OS browser instead of the iframe so it
  never races the loopback port.

- **Cleaner API error messages**: failed API calls now surface just the server's
  human-readable `detail` text (e.g. *"The linked meeting has no Teams join
  link…"*) instead of the raw `400 Bad Request: {"detail":"…"}` envelope. The
  HTTP status is only shown as a fallback when the response carries no detail.

- **Live record controls moved into the Transcript tab**: the **Record** button,
  capture-device picker, **+ mic** mix-in and meeting **language** now live pinned
  at the top of the **Transcript** tab (instead of the session toolbar), so they
  stay visible even when the transcript grows to fill the height. Session-level
  controls — topic, features, **End session**, archive and delete — remain in the
  toolbar above the tabs.

- **Sidebar auto-scrolls to the active item**: opening a topic, chat, agent,
  live session, workspace, or Kanban project from a deep link, the ⌘K command
  palette, or a search now scrolls its sidebar list so the selected row is
  brought into view instead of being left off-screen. Rows already visible stay
  put.

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

- **Orphaned `running` agents now recover on boot, and a degraded runtime is
  visible.** A dev auto-reload (or any process death) mid-turn used to leave an
  agent pinned in `running` with no live worker behind it, because the boot-time
  reap and the watchdog only ran *after* the SDK client successfully started —
  so if the client failed to come up, the row stayed stuck forever and never
  timed out. The reap (`running` → `interrupted`, resumable) now runs early on
  **every** boot, before and regardless of the SDK client, so orphans are always
  un-stuck. Settings also gains an `agents_runtime_started` signal (distinct from
  the stateless `agents_available` probe) with a warning banner when the runtime
  is installed but didn't actually start in this process — telling you to restart
  to recover instead of silently doing nothing.
- **Agents no longer brick when the default model goes stale.** The Copilot
  runtime's model catalogue rotates over time, so a model id that was valid when
  it was saved (e.g. a since-retired `claude-sonnet-4.5`) can vanish. Passing a
  now-unknown id to the SDK fails the whole turn at session build, and because a
  turn is dispatched as a detached background task the exception was **swallowed**
  — the agent hung forever on its "Sending…" spinner with no error. Three fixes:
  the factory default is now **`auto`** (the runtime picks a current model, so it
  can never go stale); a per-turn guard **downgrades a vanished model pin to
  `auto`** before the session is built (leaving `auto` and still-valid pins
  untouched, and never masking a transient runtime outage); and any turn-dispatch
  error is now surfaced as a **`failed`** status with the error text instead of a
  silent hang.

- **A stale WorkIQ / Agent 365 sign-in no longer strands you behind a 409.**
  When a manual sign-in was orphaned — its popup closed, tab reloaded, or the OS
  browser walked away (the standalone-PWA/OS-browser flow has no popup to watch
  in the first place) — the backend kept the shared *auth-family* lock parked on
  the loopback callback for up to 180s, so every retry was refused with **"A
  WorkIQ … sign-in is already in progress"** with no visible flow to finish or
  cancel. Now an explicit interactive retry **preempts** the stale flow (it
  signals it to abort, frees the loopback port, and takes over once the lock
  releases within ~5s); a genuinely near-complete sign-in whose redirect already
  landed is still left to finish, and silent/auto background passes never disturb
  a live flow. The SPA also fires a best-effort `sendBeacon` cancel on page
  unload (`pagehide`), so a reload or close releases the family lock immediately
  instead of orphaning it.
  splitting topics into collections narrowed the app's single topic tree to the
  active collection, and that tree is what the rest of the UI reads to *resolve*
  a topic — not just what the sidebar renders. So the **Live session topic
  picker listed only the current collection's topics** (both when linking an
  existing session and when starting one), the Live header's *open topic* link
  and the agent header's topic chip disappeared or fell back to a literal
  "Topic" whenever the linked topic lived elsewhere, stream-completion
  notifications lost their title, and the Topics unread badge and tab title
  undercounted. The tree is global again and the collection filter is applied
  where it belongs — the sidebar and the *parent topic* pickers — so switching
  collections is now instant client-side filtering rather than a refetch. Since
  the app-wide topic pickers (Live session linking and agent topic association)
  now span every collection, each root topic in them carries its collection's
  accent dot and name so same-named branches are easy to tell apart.

- **The composer model picker served a frozen catalog**: the shared model store
  fetched `/api/llm/models` exactly once per app lifetime and nothing ever
  invalidated it, so a list captured on first launch stayed pinned forever.
  Because the desktop webview never reloads, that snapshot could be weeks stale
  — showing models the provider has since retired while hiding newly added ones
  (MAI-Code-1-Flash was missing for exactly this reason). Since the model
  dropdown moved out of Settings into the composer, that store is the *only*
  model list in the UI, so there was no way to refresh it short of restarting
  Precursor. The catalog is now refetched when it is older than five minutes and
  the picker is opened, Settings seeds the shared store on load and on
  **Apply & refresh models** (previously the button only refreshed the settings
  panel's private copy, despite its label), and saving a new GitHub token forces
  a refresh because a different account can mean a different catalog.

- **A long-running instance served a stale Copilot model catalogue**: the
  `gh auth token` result was cached for the whole process lifetime, and GitHub
  scopes entitlements — including which models the picker may offer — to the
  token itself. An instance left running therefore kept serving the catalogue as
  it looked when it first resolved a token, offering models that had since been
  retired while hiding newly added ones; `MAI-Code-1-Flash` was missing for
  exactly this reason, and the only cure was restarting Precursor. The CLI token
  is now re-read when it is older than five minutes, and saving a GitHub token in
  Settings drops the cached one immediately.

- **WorkIQ sign-in in the installed PWA**: clicking **Sign in** (the banner or
  Settings) from Precursor running as an installed standalone PWA did nothing —
  standalone display mode heavily restricts `window.open`, so the script-opened
  sign-in popup couldn't be created, steered to the authorization URL, or
  auto-closed. The manual sign-in now detects standalone PWA mode and drives the
  OAuth flow through the **OS default browser** instead (the same surface the
  hands-free re-auth already uses), clearing the banner over SSE on success.

- **Noisy WorkIQ sign-in timeouts**: an interactive WorkIQ sign-in the user never
  completed (walked away or closed the tab without the popup's proactive cancel
  firing) used to dump a misleading `ERROR ... OAuth flow error` traceback from
  the MCP SDK and then fail the endpoint with an opaque `502 Bad Gateway`. That
  timeout is now a dedicated, benign `WorkIQAuthTimeoutError`: its SDK traceback
  is suppressed (like the silent-pass and background `needs_auth` signals) and the
  reauthenticate endpoint re-surfaces the manual sign-in banner (a normal
  `interaction_required` response) instead of a gateway error.


  briefly showed the opening user message twice. The backend persists the user
  turn immediately, so a panel mounting mid-stream could race a first-page fetch
  that already included it while the same turn also lived in the live buffer.
  Merging the persisted window with the streaming buffer now dedupes by message
  id, so the first turn renders once.

- **Stale WorkIQ sign-in banner across other windows**: when a WorkIQ sign-in
  was renewed in one window (its popup, the OS-browser tab the hands-free flow
  self-opens, or a silent pass), every *other* open window kept showing the
  "WorkIQ needs you to sign in" banner — they never made the request, so nothing
  told them the credentials were fresh. The reauthenticate flow now broadcasts a
  cross-window `mcp.auth_resolved` event on success, so those windows drop the
  banner (and any "Signing in…" state) immediately, without a reload.

- **Spurious WorkIQ sign-in prompt when preview mode is off**: an agent turn
  whose `workiq` tool call errored would surface a "WorkIQ needs you to sign in"
  banner even with preview mode disabled — where WorkIQ runs as local stdio with
  no OAuth, so the sign-in couldn't proceed (clicking it returned "Enable WorkIQ
  preview mode before signing in"). The failed-tool auth check now short-circuits
  unless preview mode is on, so routine stdio tool errors no longer nag for an
  impossible sign-in.

- **Live session opened on the wrong tab**: because the split-panel layout
  (including the active tab) is persisted globally, a freshly created Live
  session would open on whatever tab was last active — typically **Summary**
  from a previous session — instead of the transcript. New sessions now land on
  the first tab so you start at the top of the panel.

- **Microphone indicator stuck on after a Live recording**: stopping a recording
  released the captured audio tracks only from inside the Speech SDK's
  stop/close callbacks, which may never fire when the socket is already gone —
  leaving the browser/OS capture indicator lit until a page reload. Teardown now
  stops the tracks synchronously, so the indicator clears the instant you stop.

- **Double-started Live recording duplicating the transcript**: starting a Live
  recording twice at nearly the same instant (e.g. a quick double-click on
  **Record**, or a second view racing to start during a transition) could spin
  up two capture sessions against the same meeting audio — every phrase was
  transcribed twice, and stopping one left the other orphaned and still
  streaming. A single **app-wide recording lock** now guarantees only one live
  transcription runs at a time: a second start is refused (with a clear message)
  until the active one is stopped. This complements the existing per-view
  double-click latch by also covering distinct transcriber instances.

- **WorkIQ sign-in stuck on "Signing in…" when another Precursor window is
  open**: the OAuth callback uses a *fixed* loopback port
  (`127.0.0.1:12798`, matching the registered `redirect_uri`), so only one
  process per machine can run it. With several Precursor instances open (e.g.
  multiple worktrees), a sign-in launched while another instance already owned
  the port would clear the stored tokens and then hang on "Signing in…" until
  the 300s callback timeout — its browser redirect delivered to whichever
  instance held the port — leaving the server unauthenticated. The interactive
  sign-in now **preflights the loopback port** and fails fast with a clear,
  actionable **409** ("The WorkIQ sign-in port 12798 is already in use — another
  Precursor window or app is signing in…") *before* touching the existing
  tokens, so a still-usable session is preserved and the banner shows what to do
  instead of stranding. The hands-free silent pass simply defers to the manual
  banner when the port is busy. To further limit contention, **closing the
  sign-in popup now cancels the flow** (the SPA calls a new
  `POST /api/mcp/servers/workiq/reauthenticate/cancel`), so an abandoned sign-in
  releases the port at once instead of squatting it, and the interactive
  callback timeout is trimmed from 300s to 180s as a backstop.


  iframe; when framing or third-party cookies block it (or there's no live SSO
  session) the loopback never fires and the pass times out. That timeout was a
  plain `RuntimeError`, so the MCP SDK logged a full `ERROR OAuth flow error`
  stack trace on startup even though the pass is a *handled* fallback to the
  manual "Sign in" banner. A silent-pass timeout now raises the same
  `WorkIQInteractionRequiredError` as Entra's `interaction_required` — kept out
  of the logs by the existing suppression filter — while a genuine *interactive*
  (user-driven) sign-in that times out still surfaces loudly.

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

### Fixed

- **MCP: Agent 365 servers never attached to agent sessions**: `workiq-teams`
  and `workiq-user` were rejected by name when an agent turn resolved its bearer
  token, so despite being signed in they were dropped from the agent's tool
  catalog, their tools silently went missing, and a sign-in prompt was raised
  that **signing in could never clear** — the same name gate blocked the retry,
  so the prompt came back on every rebuild. Bearer resolution and tool-failure
  attribution now go through the OAuth server registry, which covers every
  OAuth-protected built-in rather than the WorkIQ preview alone.

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
