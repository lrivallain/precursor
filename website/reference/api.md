---
title: API reference
---

# API reference

::: info Coming soon
A full, generated API reference is on the roadmap. This page will document the
`/api/*` surface — endpoints, request/response schemas, and the SSE event stream —
generated from FastAPI's OpenAPI schema so it stays in lockstep with the code.
:::

Until then, the fastest way to explore the API is the **interactive OpenAPI docs**
that FastAPI serves for a running instance.

## Interactive docs (live instance)

With Precursor running locally, open:

- **Swagger UI** — [`/api/docs`](http://127.0.0.1:8000/api/docs)
- **ReDoc** — [`/api/redoc`](http://127.0.0.1:8000/api/redoc)
- **OpenAPI schema (JSON)** — [`/api/openapi.json`](http://127.0.0.1:8000/api/openapi.json)

(Adjust the port to match your `--port`.)

::: tip
FastAPI's interactive docs live under `/api/*` so the root `/docs` path can
serve this documentation site **in-app** — see
[Serving the docs in-app](#serving-the-docs-in-app) below.
:::

## Serving the docs in-app

This documentation site (the VitePress project in `website/`) is also served by
the app itself at **[`/docs/`](http://127.0.0.1:8000/docs/)**, so you can read it
without leaving Precursor (there's a **Documentation** entry in the command
palette and the About dialog).

- **Production / one-port `precursor`** — the site is pre-built with base
  `/docs/` (`make docs`) and bundled into the wheel; the backend serves it
  statically at `/docs/*`, resolving VitePress clean URLs.
- **`precursor --dev`** — a live VitePress dev server runs on a hidden port and
  the SPA's Vite proxies `/docs` to it, so editing any `website/**` markdown
  **hot-reloads** in the browser.
- **GitHub Pages** is unaffected: it builds the same source with the default
  base `/` in its own workflow.

## Surface at a glance

The JSON API lives under `/api/*`. Routers are grouped by domain:

| Area | What it covers |
| --- | --- |
| `topics` | CRUD for topics, the topic tree, read/unread state, and per-topic messages. Reads carry the topic's immutable `public_id`; `GET /by-slug/{slug}` and `GET /by-public-id/{public_id}` back the `/topics/…` and `/t/{uuid}` routes. `GET /api/topics` and `GET /api/topics/archived` accept `?collection_id=`, and `GET /api/topics/tree` already did. `GET /{id}/chats` lists the topic's non-archived [side chats](/features/chats#side-chats), newest first: `id`, `slug`, `title`, `parent_message_id`, `from_reply`, `unread_count`, `message_count`, `last_message_at`, `created_at` and `reminder` (`{remind_at, status}` or `null`). `POST /{id}/chats` `{message_id?}` starts one and returns the chat with `201`. `message_id` must be an assistant reply of that topic: `404` otherwise, or `422` if it isn't an assistant reply. |
| `topic-summary` | The topic's editable [status brief](/features/topic-summary), as a sub-resource: `GET`/`PUT`/`DELETE /api/topics/{id}/topic-summary` (read — `null` when the topic has none — save an edit, drop it), `POST …/visibility`, `POST …/generate` (refresh from the conversation, notes and attached filenames), `POST …/resolve` (`{revision, accepted: [indices], reviewed?: [indices]}`) and `POST …/items` (append a `todo` or `important` line). Each hunk carries `base_start`/`base_end` line offsets into the *current* content, so a client can render it merged in place rather than as a detached list. Resolve without `reviewed` decides the whole proposal; with it, only those changes are decided and the rest remain pending. Use `reviewed: [i], accepted: [i]` to accept one change, or `reviewed: [i], accepted: []` to reject it. Use the returned revision and re-numbered hunks for the next decision. Reads include an opaque `revision`: send it in the `PUT` body or as a `DELETE ?revision=…` precondition; it is required for resolve. Stale writes return `409`; invalid indices or accepted indices outside `reviewed` return `422`. Committed changes emit `topic-summary.changed` with `topic_id` on `/api/events`. Distinct from `POST /api/topics/{id}/summary`, which summarises the *linked GitHub issue*. |
| `collections` | CRUD for [collections](/features/collections); deleting one re-homes its topics via `?reassign_to=`. |
| `chat` | Streamed chat (`.../messages/stream`) over Server-Sent Events, for topics (`/api/topics/{id}/messages/…`) and chats (`/api/chats/{id}/messages/…`). `POST …/messages/compact` `{instructions?}` summarises the conversation into a [compaction marker](/features/context-compression): a `system` message with `kind: "compaction"`, returned with `201`. The model's history starts from the latest marker, and deleting the marker undoes it. It answers `409` while a reply is generating or when there's nothing to compact. `GET …/messages/context` estimates the history the next turn sends: `tokens`, `by_role`, `saved_by_hygiene`, `messages`, `compacted`, `compaction_id` and `compacted_messages`. Messages carry `kind` (`null` for ordinary rows). `GET …/messages/turns` returns the [timeline](/features/rewind) index, oldest first, one entry per user prompt: `message_id`, `last_message_id` (the last row before the next prompt), `created_at`, a 200-character `prompt` excerpt, the first assistant text as `reply`, `has_compaction` and `agent_session_id`. `POST …/messages/rewind` `{from_message_id, through_message_id?}` deletes the turn that `from_message_id` opens (it must be a user prompt, otherwise `422`) and every later row up to `through_message_id`, attachments included. It returns `{deleted}`, `404` for a message from another conversation, and `409` while a reply is generating. |
| `chats` | Quick throwaway chats, including read/unread state. `POST /{id}/promote` turns one into a topic and takes `?collection_id=` for where it lands. `POST /{id}/messages/suggest-name` re-derives the title from the transcript; `POST /api/chats` takes `autoname: true` to mark the supplied title a placeholder the server may replace, and an optional `role_id` to assign an assistant [role](/features/skills-memory) at creation (omit or `null` for the default role). Chat reads carry the [side chat](/features/chats#side-chats) link: `parent_topic_id`, `parent_topic_title`, `parent_message_id` and `seed_content`, the copy of the reply it started from. All are `null` for an ordinary chat. |
| `commands` | The `/slash` [commands](/features/skills-memory) a topic composer can run. |
| `attachments` | Upload / fetch [attachments](/features/attachments) on a topic or chat. |
| `settings` | Runtime settings and provider/GitHub configuration (secrets never echoed — bar the [OpenAI-compatible endpoint](/features/openai-endpoint)'s key, returned in clear as `openai_proxy_key` so it can be pasted into more clients). `POST /api/settings/openai-proxy/key` replaces that key. |
| `llm` | The provider catalogue and the models the active provider offers. Each model carries its `context_window`, `max_output_tokens`, `supported_reasoning_efforts` and `vision` when the provider advertises them; each provider says whether the OpenAI-compatible endpoint can relay to it (`openai_proxy`). |
| `openai` | The [OpenAI-compatible endpoint](/features/openai-endpoint) under `/api/openai/v1`: `GET /models`, `GET /models/{id}` and `POST /chat/completions` (streamed or not), in OpenAI's wire format and error shape. Off by default, and every request needs `Authorization: Bearer <openai_proxy_key>`; answers `404` when switched off and `503` when the active provider can't be relayed to. |
| `me` | The connected GitHub identity for the sidebar persona, plus `GET /api/me/copilot` for Copilot AI-credit usage (both degrade to `null` when no token is configured). |
| `github` | Issue/label/comment operations behind topic linking. |
| `github/projects` | GitHub Projects v2 columns and cards behind the [Kanban board](/features/kanban). Contributed by the `precursor-kanban` [plugin](/features/plugins) — absent when it isn't installed. |
| `mcp` | Tool-server registry, enable/disable, and OAuth (re)authentication. `GET /api/mcp/auth/diagnostics` returns the [WorkIQ sign-in trace](/features/mcp#when-a-sign-in-prompt-needs-explaining) — settings in force, a per-credential fact sheet (token/refresh-token presence, expiry, renewal lead, idle time, state) and the recent auth-episode records. Token values never leave the process. |
| `skills` / `memories` | Skill enablement and long-term memory. |
| `roles` | Assistant [roles](/features/skills-memory) (persona presets). |
| `…/schedule` | Recurrence and **Run now**, as a sub-resource of the thing being scheduled (`/api/topics/{id}/schedule`, `/api/agents/{id}/schedule`) rather than a top-level router. A schedule may carry [several recurrence rules](/features/scheduler#several-cadences-on-one-item): payloads accept a `rules` array of `{interval_seconds, days_of_week, run_at_minute, timezone}` objects and reads return one. The legacy flat fields still work and mirror the **first** rule — sending `rules` replaces the whole set, while sending only the flat fields patches the primary rule and leaves the extras alone. `next_run_at` is the earliest fire time across the set. |
| `reminders` | One-shot [reminders](/features/scheduler) that resurface a topic or chat. |
| `agents` | Agent sessions, timelines, and read/unread state, plus [orchestration](/features/agents-mode/orchestration): `GET /metrics` (fleet rollup) and `GET /inbox` (everything waiting on you), `blueprints` CRUD + `/instantiate`, per-agent `/start` (launch a parked agent), `/runs` and `/runs/{runId}` (the agent's [execution history](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run) — trigger, status, capability snapshot, per-run token spend), `/events` (the transcript; every event carries the `agentRunId` it belongs to, and `?agentRunId=` narrows the reply to one run — a run belonging to another agent is a `404`. The reply is a page: `events` plus a `cursor`, the volatile `pending` approval cards, the volatile `thinking` (`{text, active}` — the round's thinking as it streams, `active` while nothing of the answer or a tool call has followed; `null` once its complete reasoning event is archived), and a `reset` flag. Streaming frames (message, thinking and tool-argument deltas, byte counters) feed only that live view and are never archived. Because the archive only grows between rewinds, a live view sends the previous `cursor` back as `?after=`, with the page's `epoch` as `?epoch=`, and gets only the steps since — omit them for the whole transcript. `reset` means the cursor no longer addresses this transcript (cleared, rewound, or another run), so `events` is a replacement rather than a delta. User prompts carry their SDK `event_id`, and `rewindable_run_ids` lists the runs whose prompts can be rewound: the current run and earlier runs of the same Copilot session, none while a workflow step drives it), `GET /rewind/preview?event_id=` (checks a rewind like `POST /rewind` and changes nothing; returns `{event_id, file_tracking, files_available, unavailable_reason, file_count, files: [{path, change_type, lines_added, lines_removed}]}`), `POST /rewind` `{event_id, mode?}` ([rewind](/features/rewind#in-agent-sessions) the current session: drops that prompt and everything after it from the Copilot session and the transcript, irreversibly. `mode` is `conversation` (default) or `conversation-and-files`, which first [restores the files](/features/rewind#restoring-files) the dropped turns changed. Returns `{events_removed, sdk_events_removed, outcome, restored_files, skipped_files: [{path, reason}]}`. `409` while a turn is in flight, for a workflow-driven run, when the session can't return to that turn, or for files mode on a session that didn't track file changes. `400` for a prompt outside the current session. `502` when the Copilot session fails the cut: the conversation is left as it was, and the message says whether files were put back), `/artifacts` (list/create plus `GET /artifacts/{id}` and `GET /artifacts/{id}/raw` for a single artifact and its kind-typed raw body — a `link` artifact redirects to its URL; the list accepts `?runId=` to scope to one run), `/state` (the [durable cross-run scratchpad](/features/agents-mode/artifacts-state#durable-state-the-private-scratchpad): `GET` to list, `PUT` to upsert by key, `DELETE /state/{key}` or `DELETE /state` to reset), and `/triggers`, and the public `POST /hooks/{token}` webhook. Three routes are deliberately reachable while Agents mode is off, since they are how it gets turned on: `GET /runtime` (capability probe, why it's unavailable, and any provisioning job in flight), `POST /runtime/cli` (download the native Copilot CLI — returns immediately; poll `GET /runtime` for progress) and `POST /runtime/restart` (hand off to the supervisor; `409` when the instance isn't supervised). |
| `workflows` | [Workflow](/features/workflows) definitions, steps, lifecycle (`/run`, `/pause`, `/resume`, `/cancel`, `/retry`), run traces, per-step replay, approval and tool-permission decisions, pipeline state, schedules, and the public `POST /hooks/{token}` webhook. See the [workflows API reference](/features/workflows/reference#api-surface). |
| `workspaces` | [Workspace](/features/workspaces) clones, the sandboxed file tree, file reads/writes, and workspace chat. `DELETE /{id}/folder?path=` removes a folder with its contents (`400` for the workspace's own folder or a path outside it, `409` for the folder holding the definition files). The list leads with the one holding the definition files (`hosts_definitions: true`), which can't be deleted while it holds any. Git: `GET /{id}/git/status` reports the checked-out `branch`, `detached`, `upstream` (on origin; null while unpublished), `ahead`/`behind`, `merging`, and per file its `code`, `orig_path` (renames), `conflicted` and `browse_path` (relative to the workspace's subdir). `POST /{id}/git/fetch` updates ahead/behind without touching files; `/git/pull` fast-forwards; `/git/commit` `{message, paths?}` commits locally; `/git/push` pushes, publishing the branch the first time; `/git/commit-push` does both; `/git/discard?path=` restores a file from HEAD (deleting one HEAD doesn't have); `GET /git/diff?path=`; `GET /git/file-versions?path=&original_path=&base=&head=` returns `{original, modified, binary, too_large}` for the diff editor — the file at `base` (a rename's `original_path`) and in the working copy, or at `head` (null where it doesn't exist; over 2 MB only the flag). `base` defaults to `HEAD` for the working copy; with `head` and no `base` (a root commit) the original is empty. Revisions are commit ids or `HEAD`, nothing else (no `^`, `~`, branch names). Branches: `GET /git/branches` → `{current, local: [{name, upstream}], remote, remote_error}` (the remote's branches come from `git ls-remote`; names git wouldn't accept as branch names are left out); `POST /git/switch` `{name}` checks out a local branch, or one only the remote has (tracking it); `POST /git/branches` `{name}` (201) creates a branch from HEAD without an upstream and checks it out. Both return 409 when something could be lost or the state forbids it (uncommitted changes — the files are named — a merge in progress, a detached HEAD, a name already taken here or on the remote), 404 for an unknown branch, 400 for an invalid name; the workspace's `branch` follows. Merging: `POST /git/merge` merges the remote's branch into the checked-out one with a merge commit (`ok: false` with `status.merging` when it stopped on conflicts; 409 on uncommitted changes, a detached HEAD or a merge already running). `GET /git/conflict?path=` returns `{kind, base, ours, theirs, has_base, has_ours, has_theirs, binary, too_large}` for a conflicted file (index stages 1–3; a side may be missing). `POST /git/resolve` `{path, side?}` marks it resolved: as saved (409 while conflict markers remain) or keeping `ours`/`theirs` (a side that deleted the file deletes it). `POST /git/merge/complete` commits the merge (409 while a file is conflicted); `POST /git/merge/abort` undoes it. Names are refused, not trimmed, with surrounding whitespace or control characters; a new branch can't be named like another kind of ref (`refs/…`, `heads/…`, `origin/…`, `HEAD`…). History: `GET /git/log?limit=&skip=&path=` pages the checked-out branch (`limit` 1–100, default 50; `skip` ≥ 0) as `{commits: [{sha, short_sha, author, date, subject, parents}], has_more}`, following one file across renames with `path`. `GET /git/commits/{sha}` adds `body`, `parent` (the first parent, resolved on the server; null for a root commit) and `files` `[{status, path, orig_path}]` compared with that parent (404 for an unknown commit). Status also carries `head`, the checked-out commit. Pull and push use the branch checked out (409 on a detached HEAD). Paths are relative to the repository root and rejected (400) if they leave it or touch `.git`. |
| `live` | [Live sessions](/features/live-sessions) — transcript segments, insights, notes, summary, and attendees. `POST /{id}/summary` takes an optional `{template, language}` body: a [summary template](/features/live-sessions#generating-the-summary) id and a language code (`fr`, or `""` for the session's language); each omitted one falls back to the last used, and both are remembered for the next recap (echoed back as `template` / `language`). An unknown template is a `400`, an unsupported language a `422`. `GET /summary-templates` lists the templates (`id`, `name`, `description`, `source: builtin \| file`, `path`, `overrides_builtin`), the `problems` of template files left out, the `languages`, the `last_template` / `last_language`, and the definitions `folder` and `workspace`. `PUT /summary-templates/selection` `{template, language}` remembers the picker's choice (`204`). `POST /summary-templates/files` `{template, duplicate?}` returns the definition file to edit a template in — `{id, path, created, folder, workspace, workspace_path}` — saving a built-in there first (same id, so it replaces it), or with `duplicate` saving a copy under a new id. `GET /{id}/transcripts` lists the linked Teams meeting's transcription sessions (id + `created_at`/`ended_at`, oldest first, fail-closed with a `detail`), and `POST /{id}/summary/from-transcript` takes an optional `transcript_ids` body to summarize the chosen session(s) — several are stitched in chronological order; omitted, the most recent is used. It accepts `template` and `language` too. |
| `stt` | Short-lived Azure Speech token minting for live sessions. |
| `search` | The literal (substring) cross-entity lookup. The ⌘K palette falls back to it when [Precursor IQ](/features/iq) is disabled. |
| `iq` | [Precursor IQ](/features/iq). `GET /api/iq/retrieve?q=&limit=&sections=` returns ranked `hits` (each with a citation `id`, `section`, `entity_id`, `ref`, topic `path`, `url`, `field`, `snippet`, `excerpt`, `score`) plus a grounding `markdown` block with `[^n]` citations, the `lexical_backend` (`fts5` / `tsvector` / `like`), whether `semantic` vectors took part, and the `pending` change-queue depth. `sections` restricts results to entity kinds (`topics,chats,agents,live,memory`); an unknown one is a `422`. `POST /api/iq/ask` `{question, sections?}` returns a cited `answer`, the `model`, the `citations` it used and all `sources` (no model call when nothing matches; `502` when the provider fails). `GET /api/iq/status` returns index counts and embedding state, and `POST /api/iq/reindex` queues everything again and rebuilds in the background. All but `status` answer `404` when `PRECURSOR_IQ_ENABLED=false`. |
| `refine` | One-shot text rephrasing for the notes panel and composer. |
| `stats` | Token-usage rollups and the system footprint for **Settings → Usage stats**, plus the [storage cleanup](/features/storage) cockpit: `GET /api/stats/cleanup` previews what each retention sweep would free (a dry run — nothing is deleted), `POST /api/stats/cleanup/{key}` runs one on demand, and `POST /api/stats/compact` `VACUUM`s the database so freed pages return to the filesystem. |
| `transfer` | [YAML export/import](/features/transfer) of agents and workflows: `GET /workflows/{id}` and `GET /agents/{id}` download a definition; `POST /preview` reports name conflicts without writing; `POST /import` applies them with a per-agent `replace` / `create` / `link` resolution. |
| `definitions` | **Work in progress** — agents and workflows declared in [definition files](/features/definitions) (opt-in files mode), and live [summary templates](/features/live-sessions#your-own-templates). `GET /check` reports on the definitions folder: per-file and cross-file errors (invalid files, duplicate ids, dangling agent paths), warnings for role and MCP server names this instance doesn't have, and which database agents and workflows are linked to a file (`linked` is null for a summary template, which has no row). `POST /export` writes a file for every agent and workflow that has none yet (`?overwrite=true` regenerates existing ones, except in files mode); it needs Agents mode on. `GET /file-issues?workspace_id=&path=` returns the check's findings for one file opened in the Files section, each placed in the text with `line`, `column`, `end_line`, `end_column` (1-based, end exclusive, UTF-16 columns as in Monaco; null when unknown). `GET /schema/{kind}` (`agent`, `workflow` or `summary`) serves the JSON Schema published in `docs/schemas/`, which the Files editor validates and completes against. In files mode (`PRECURSOR_DEFINITIONS_SOURCE=files`), agent and workflow payloads carry `definition` — `{state: file \| invalid \| none, path, message}` — and agent and workflow runs carry `definition_path` / `definition_hash`. `definition.review` lists permission changes a file makes beyond what was accepted; `POST /accept` `{kind, id, content_hash}` accepts them. The [migration](/features/definitions#getting-there-from-today-s-data): `GET /status` says cheaply how many agents and workflows the database still declares (the homes' invitation), `GET /migration` previews it, `POST /migrate` `{acknowledge}` writes, verifies and switches to files mode (`ok: false` with `mismatches` when verification fails — nothing switched), `POST /revert` copies the files back into the database and switches back, `GET /migration/items/{kind}/{id}` returns the file as it would be written next to the one on disk, and `POST /finalize` `{confirm: true}` cleans the declarations out of the database — irreversible; `stage` in the preview is then `finalized`. |
| `plugins` | Descriptors for frontend extensions contributed by plugins, and the [plugin catalogue](/plugins): `GET /api/plugins/catalog` returns the bundled directory of installable plugins, each annotated with whether this instance already has it. Reading it executes nothing and is ungated; installing from it goes through the same three-gate `POST /api/plugins/install` as any other package. `POST /api/plugins/install` takes `{package, version?}`: `package` is a name, a GitHub repository link (installed from a release's wheel) or any requirement, and `version` picks a release for the first two — omitted, a name gets a floor at its newest PyPI release and a repository its newest release. `GET /api/plugins/versions?package=` lists every release of a name or repository, each with the requirement that installs it; `GET /api/plugins/updates` reports each installed plugin's newest release from its own source (PyPI or GitHub); `POST /api/plugins/installed/{id}/upgrade` `{version?}` moves one plugin, and only it, to a release (both lookups are cached; `?refresh=true` skips the cache). `GET /api/plugins/installed` includes each plugin's `source`. Install, upgrade and uninstall (`DELETE /api/plugins/installed/{id}`) answer with `restart_required` and `host_upgrade` — the nightly Precursor itself moved to when the build it was installed from is no longer published, otherwise `null`. |
| `drawio` | Status and on-demand install of the self-hosted [draw.io editor](/features/workspaces#editing-diagrams) (`GET /api/drawio/status`, `POST /api/drawio/install`). |

Health and version:

- `GET /api/health` — liveness + version.
- `GET /api/version` — the CalVer version (derived from git tags at build time).
- `GET /api/version/check` — whether a newer build is published on the active
  channel, plus how this instance was installed. Read-only on purpose: applying
  an update replaces the process serving the request, so it belongs to
  [`precursor service update`](/features/background-app#updating-in-place).
  Cached; pass `?force=true` to re-ask GitHub.

::: warning One route lives outside `/api/*`
`GET /raw/{slug}/{path}` serves a [workspace](/features/workspaces) file straight
from its working tree, so HTML renders and relative links inside a file resolve
naturally. It is **read-only and unauthenticated by design**, like the rest of
this single-user app — one more reason to keep Precursor bound to loopback.
:::

## Addressing an agent

An agent's URL-safe identity is its **`public_id`** — the UUID behind
`/agents/{uuid}` deep links, `/agent <uuid>` nudges and
[transfer](/features/transfer) lookups. It is stable for the life of the agent
and independent of any Copilot SDK session handle, which now belongs to an
individual [run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)
rather than the agent. `AgentSessionRead` also embeds a nullable `current_run`,
so one fetch tells you both what the agent *is* and what it's doing right now.

## Real-time events

`GET /api/events` is a Server-Sent Events stream the SPA subscribes to for
cross-window sync. Requests carry an `X-Client-Id` header so the originating
client's own events are filtered back out.

| Event | Fires when |
| --- | --- |
| `topic.changed` | A topic's metadata or state is mutated. |
| `chat.changed` | A chat's own metadata is mutated — most often [auto-naming](/features/chats#chats-name-themselves) replacing its placeholder title. Broadcast without client-id filtering in that case, since the window that started the chat is the one that needs the new title. |
| `message.changed` | A message is added or edited in a topic or chat. |
| `stream.started` | A chat turn begins. |
| `stream.ended` | A chat turn completes. |
| `read.changed` | A conversation is marked read (distinct from `message.changed`). |
| `reminder.changed` | A [reminder](/features/scheduler) is created, fires, or is cleared. |
| `agent.changed` | An [agent](/features/agents-mode) session's state or event stream moves. |
| `meeting.changed` | A [live session](/features/live-sessions) is created, renamed, ended, or deleted. |
| `workflow.changed` | A [workflow](/features/workflows) step advances, its status flips, or its definition is edited. Carries the run status and workflow name so a client can raise a notification without re-fetching. |
| `mcp.auth_required` | An [MCP server](/features/mcp) needs an interactive sign-in. |
| `mcp.auth_url` | The OAuth authorization URL to open for that sign-in. |
| `mcp.auth_resolved` | An MCP server sign-in completed. |
| `mcp.server_state` | A server finished [warming up](/features/mcp#startup-and-the-first-prompt) — carries its resolved `state` and advertised tool count. |

`agent.changed` and `read.changed` carry an `agent_run_id` alongside
`agent_session_id`, so a listener can tell *which* execution of a shared agent
moved.

### Events raised in a subprocess

The bus is per-process, but several built-in [MCP servers](/features/mcp) —
`precursor` included — run as stdio subprocesses that share the database and not
the bus. A write made there (`append_note`, `post_message`, a reminder) would be
committed and then announced to nobody.

`POST /api/events/publish` is how those children get back on the bus. The app
exports a loopback URL and a per-run token into the environment it hands each
child; the child presents the token in `X-Precursor-Event-Token` and the app
republishes the event to every window. Relayed events are broadcast with an
empty `client_id`, because the window that needs to re-render is precisely the
one whose chat turn called the tool.

Only the data-refresh types are accepted (`topic.changed`, `message.changed`,
`reminder.changed`, `read.changed`, `agent.changed`, `meeting.changed`,
`workflow.changed`); anything that makes a window act on the payload — such as
`mcp.auth_url`, which steers a popup — is refused with a `400`, and a missing or
wrong token is a `403`. The worst a relayed event can do is make a window
refetch data it already has.

Streamed chat responses are their own SSE stream, delivering text deltas and
tool-call events for a single turn. A turn that dies (a provider rejection, the
tool-round cap) emits an `error` event *and* persists an `Error: …` system
message, so the failure is still there after a reload.

A `reasoning` event (`{content}`) carries a chunk of the model's
[thinking](/features/topics#watching-the-model-think); it arrives ahead of the
`delta` events of the same round. The thinking of each round is stored on the
assistant message it produced — the `tool_calls` round or the final answer — and
returned as `reasoning` (`null` when the model surfaced none).

A `tool_result` event carries the call id, name, arguments, result text and
error flag — plus `link` (`{slug, path}`) when the tool read or wrote a
[workspace](/features/workspaces) file, which the UI turns into an **Open** chip.
The same object is stored on the tool message's `tool_calls` metadata, so the
chip survives a reload without re-reading the result body.

`POST /api/workspaces/{id}/chat/stream` — the [workspace](/features/workspaces)
assistant — streams the same `delta`, `tool_calls`, `tool_result` (with `link`),
`mcp_auth_required`, `system`, `error`, `done` and `suggestions` events. It is
ephemeral: the client sends the prior turns as `history`, nothing is persisted,
so its events carry no message ids and it sends no `user_message` or `usage`
event. Its token usage is still written to the usage ledger.

`POST .../messages/stream` accepts `retry_message_id` to **replay** such a turn:
instead of persisting a new user message it reuses that one — attachments and
all — and deletes every message recorded after it. The id must name a user turn
of the same container (`400` otherwise, `404` when unknown).

Stopping a turn disconnects the stream, so the backend never persists its final
answer or the results of tools still running. The client records them with
`POST .../messages/stopped`: `content` is the text received so far and
`tool_call_ids` names the calls of the latest tool round that never returned —
either may be omitted, not both (`422`). An optional `reasoning` is stored with
`content` as that reply's thinking. Each such call gets a tool row whose
metadata carries `"stopped": true`, skipping ids the round didn't issue or that
already have a result, so the round still replays on the next turn. The response
is the list of rows created, oldest first.

::: tip Contributions welcome
Want to help build the generated reference? See the
[contribution guide](/contributing/).
:::
