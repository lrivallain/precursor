---
title: API reference
---

# API reference

The JSON API lives under `/api/*`. The **OpenAPI schema is the authoritative
reference** for every route, parameter and payload; this page covers what the
schema doesn't say: conventions, behaviour, status codes and the event streams.

## Interactive docs (live instance)

| | |
| --- | --- |
| Swagger UI | [`/api/docs`](http://127.0.0.1:8000/api/docs) |
| ReDoc | [`/api/redoc`](http://127.0.0.1:8000/api/redoc) |
| OpenAPI JSON | [`/api/openapi.json`](http://127.0.0.1:8000/api/openapi.json) |

Adjust the port to your `--port`. The same schema generates the frontend's
`src/lib/generated/apiTypes.ts` (`make api-types`).

## Serving the docs in-app

FastAPI's docs sit under `/api/*` so that `/docs/` can serve this site in the
app (command palette → **Documentation**, About dialog).

| Mode | How `/docs/` is served |
| --- | --- |
| Wheel / `precursor` | pre-built with base `/docs/` (`make docs`), bundled in the wheel, served statically with VitePress clean URLs |
| `precursor --dev` | a live VitePress dev server on a hidden port, proxied by Vite; edits to `website/**` hot-reload |
| GitHub Pages | same source, base `/`, built by `pages.yml` |

## Conventions

- **No authentication.** Single-user app; keep it on loopback
  ([security model](/reference/architecture#security-deployment-model)).
- **Secrets are write-only**: reads return `*_present` booleans. Exception: the
  OpenAI-compatible endpoint's own key (`openai_proxy_key`).
- **Identifiers**: numeric `id`s are internal; topics, chats, messages and
  agents also carry an immutable UUID `public_id` used in links.
- **Status codes**: `404` unknown or wrong-container resource, `409` state
  conflict (a reply is generating, stale `revision` / `state_token`, archived
  plan, uncommitted git changes…), `422` validation, `502` the model or an
  upstream service failed. A `400` is a malformed path or name.
- **Roles** are addressed by name everywhere (`role` on topics, chats,
  workspaces, live sessions, agents, workflows; `default_role` on collections);
  an unknown name is `404`.

## Routers

| Area | Prefix | What it covers |
| --- | --- | --- |
| `topics` | `/api/topics` | CRUD, tree, read state, `by-slug/{slug}`, `by-public-id/{uuid}`; `?collection_id=` filters; `POST /{id}/archive?side_chats_too=true`. [Side chats](/features/chats#side-chats): `GET`/`POST /{id}/chats` (`{message_id?, quote?}`; `message_id` must be an assistant reply of that topic). |
| `topic-summary` | `/api/topics/{id}/topic-summary` | the [status brief](/features/topic-summary): `GET`/`PUT`/`DELETE`, `/visibility`, `/generate`, `/resolve`, `/items` ([details](#topic-summary)) |
| `collections` | `/api/collections` | [collections](/features/collections); `DELETE ?reassign_to=` re-homes topics |
| `chat` | `/api/{topics\|chats}/{id}/messages` | streaming and transcript operations ([details](#conversations)) |
| `chats` | `/api/chats` | quick and side chats ([details](#chats)) |
| `step-plans` | `/api/step-plans`, `/api/steps` | [Step by step](/features/step-by-step) ([details](#step-plans)) |
| `commands` | `/api/topics/{id}/commands` | composer [slash commands](/features/skills-memory) |
| `attachments` | `/api/attachments`, `/api/{topics\|chats}/{id}/…` | [attachments](/features/attachments) on a topic or chat |
| `settings` | `/api/settings` | runtime settings, provider and GitHub config ([model settings](#model-settings)); `POST /openai-proxy/key` rotates the relay key |
| `llm` | `/api/llm` | provider catalogue; `GET /models?provider=` lists a provider's models with `context_window`, `max_output_tokens`, `supported_reasoning_efforts`, `vision`, `catalog_provider` |
| `openai` | `/api/openai/v1` | [OpenAI-compatible endpoint](/features/openai-endpoint): `GET /models`, `GET /models/{id}`, `POST /chat/completions`. Needs `Authorization: Bearer <openai_proxy_key>`; `404` when off, `503` when the provider can't be relayed. |
| `me` | `/api/me` | GitHub identity (plus `proxy_user` from `X-Forwarded-User`, display only); `GET /copilot` for AI-credit usage (`null` without a token); `GET /github-accounts` lists the `gh` logins for the Settings account picker |
| `github` | `/api/github`, `/api/topics/{id}/issue` | issue/label/comment operations for topic linking |
| `github/projects` | `/api/github/projects` | [Kanban](/features/kanban) columns and cards; only with the `precursor-kanban` [plugin](/features/plugins) |
| `mcp` | `/api/mcp` | server registry, toggles, OAuth. User servers take `auth` (`none` \| `azure_cli`), `auth_tenant`, `auth_resource`; `POST /servers/detect-auth` `{url}` probes protected-resource metadata; `GET /auth/diagnostics` is the [sign-in trace](/features/mcp#when-a-sign-in-prompt-needs-explaining) (no token values) |
| `skills` / `memories` | `/api/skills`, `/api/memories` | skill enablement, long-term memory |
| `roles` | `/api/roles/{name}` | [roles](/features/skills-memory), case-insensitive. `PATCH` edits the prompt only (renaming → `400`; `default` can't be renamed or deleted). Delete clears the role everywhere, including definition files and run snapshots; an unsafe file edit → `409`. Not transactional across files and DB: retry after an I/O failure. |
| `…/schedule` | `/api/{topics\|agents}/{id}/schedule` | recurrence and **Run now**. `rules: [{interval_seconds, days_of_week, run_at_minute, timezone}]` replaces the [set](/features/scheduler#several-cadences-on-one-item); legacy flat fields patch the first rule. `next_run_at` is the earliest across rules. Topic schedules take `approval_timeout_minutes`. |
| `reminders` | `/api/reminders` | one-shot [reminders](/features/scheduler) |
| `agents` | `/api/agents` | [details](#agents) |
| `workflows` | `/api/workflows` | definitions, steps, `/run` `/pause` `/resume` `/cancel` `/retry`, traces, replay, approvals, state, schedules, public `POST /hooks/{token}`; see the [workflows API](/features/workflows/reference#api-surface) |
| `workspaces` | `/api/workspaces` | [details](#workspaces) |
| `live` | `/api/live` | [details](#live-sessions) |
| `stt` | `/api/stt` | short-lived Azure Speech tokens |
| `search` | `/api/search` | literal substring lookup; the ⌘K fallback when [IQ](/features/iq) is off |
| `iq` | `/api/iq` | [details](#precursor-iq) |
| `refine` | `/api/refine` | one-shot rephrasing for notes and composer |
| `stats` | `/api/stats` | usage rollups and footprint; [storage cleanup](/features/storage): `GET /cleanup` (dry run), `POST /cleanup/{key}`, `POST /compact` (`VACUUM`) |
| `transfer` | `/api/transfer` | [YAML export/import](/features/transfer): `GET /workflows/{id}`, `GET /agents/{id}`, `POST /preview`, `POST /import` (`replace` / `create` / `link` per agent) |
| `definitions` | `/api/definitions` | **WIP** [definition files](/features/definitions) ([details](#definitions)) |
| `plugins` | `/api/plugins` | [details](#plugins) |
| `drawio` | `/api/drawio` | [draw.io editor](/features/workspaces#editing-diagrams): `GET /status`, `POST /install`; the editor itself is served at `/drawio/*` |

**Health and version**: `GET /api/health` (liveness + version), `GET /api/version`,
`GET /api/version/check` (newer build on the active channel and install kind;
cached, `?force=true` re-asks GitHub; read-only, applying is
[`precursor service update`](/features/background-app#updating-in-place)).

Outside `/api/*`: the SPA (`/`), the docs (`/docs/`), MCP over HTTP (`/mcp`),
draw.io assets (`/drawio/*`) and raw workspace files:

::: warning Raw workspace files
`GET /raw/{slug}/{path}` serves a [workspace](/features/workspaces) file from
its working tree so HTML and relative links render. It is **read-only and
unauthenticated by design**: another reason to stay on loopback.
:::

## Conversations

Under `/api/topics/{id}/messages` and `/api/chats/{id}/messages`.

| Route | Behaviour |
| --- | --- |
| `POST …/stream` | streams a turn ([SSE events](#streamed-turn-events)). Optional `model` override within the conversation's provider; `workspace_context: {workspace_id, path?}` tells the model which file is open (not stored); `retry_message_id` replays a user turn, deleting everything after it (`400` wrong container, `404` unknown). |
| `POST …/stopped` | records a stopped turn: `content` (text so far), `tool_call_ids` (unreturned calls of the last round), optional `reasoning`; at least one of the first two (`422`). Returns created rows, oldest first; tool rows carry `"stopped": true`. |
| `POST …/compact` | `{instructions?}` → a [compaction](/features/context-compression) `system` message (`kind: "compaction"`), `201`; deleting it undoes it; `409` while generating or nothing to compact |
| `GET …/context` | estimate of the next turn: `tokens`, `by_role`, `saved_by_hygiene`, compaction fields, `system_tokens`, `system_tools_tokens`, `mcp_tools_tokens` |
| `PATCH …/{message_id}` | `{content}` edits an assistant reply. First edit keeps `original_content`; `edited_at` set; saving the original text restores it. Non-assistant or empty → `422`. |
| `GET …/turns` | [timeline](/features/rewind) index, one entry per prompt (`message_id`, `last_message_id`, 200-char `prompt`, `reply`, `has_compaction`, `agent_session_id`) |
| `POST …/rewind` | `{from_message_id, through_message_id?}` deletes that turn and later rows up to `through`, attachments included; returns `{deleted}`; `422` if not a user prompt, `409` while generating |

Messages carry `kind` (`null` for ordinary rows; `step_user_request` /
`step_automation` for Step-by-Step drafting turns, `compaction`, `step_plan`,
…) and `public_id`. The SPA resolves
`/topics/id/{uuid}#message={message_uuid}` and `/chats/id/{uuid}#message=…`;
`/t/{uuid}` and readable routes still work.

**Approvals** ([feature](/features/approvals)): `/api/{topics|chats}/{id}/approvals`
(`GET` open rows, `POST /{approval_id}` to answer, `POST /resume` to stream the
rest of a parked turn) and `/api/approval-rules` (standing `allow` / `ask`).
Conversations carry `approval_policy` (`manual` \| `balanced` \| `plan` \|
`autonomous` \| `null` = inherit) and `pending_approvals`. Chats also return
`inherited_approval_policy` / `inherited_approval_plan_id` for model-delegated
plan discussions. Rule order: discussion → source conversation → global.

**Artifacts** ([workspace links](/features/workspaces#discussing-a-workspace)):
`GET /api/{topics|chats}/{id}/artifacts` (`workspace_slug`, `workspace_name`,
`path`, `source`: `assistant` \| `manual` \| `opened`, `exists`), `POST`
`{workspace_id, path, source?}` (idempotent), `DELETE …/artifacts/{artifact_id}`.
A successful workspace write tool call links its file automatically.

**Conversation model** (`llm_selection`, nullable, on chat/topic create, read
and `PATCH`):

```json
{
  "llm_selection": {
    "provider": "azure_foundry",
    "model": "my-gpt-4o",
    "category": null,
    "reasoning_effort": "",
    "context_tokens": 128000
  }
}
```

Exactly one of `model` or `category` (`efficiency` \| `balanced` \|
`intelligence`, with presets in that provider's scope). Unknown provider, bad
effort, extra fields or a budget outside 1,000–5,000,000 → `422`. `null` clears
it; omitting it leaves it. Side chats copy it; promotion keeps it. An
unconfigured explicit provider fails before streaming (`422` on
`GET /api/llm/models`, never a Mock catalogue; upstream failure `502`).

### Chats

- `POST /api/chats`: `autoname: true` lets the server replace the placeholder
  title; optional `role`.
- `POST /{id}/promote?collection_id=&parent_id=`: becomes a topic. A side chat
  becomes a child of its parent topic. The quoted reply is kept as
  `seed_content`.
- Side-chat fields on reads: `parent_topic_id`, `parent_topic_title`,
  `parent_message_id`, `seed_content` (all `null` otherwise).
- `POST /{id}/discard`: deletes an untouched side chat (`204`), else `409`.
- `POST /{id}/topic-note/draft` `{instructions?}` → `{text, topic_id, topic_title}`
  (nothing saved); `POST /{id}/topic-note` `{text}` files it into the topic
  (`201`). `409` without a linked topic.
- `POST /{id}/messages/suggest-name`; `GET /by-public-id/{uuid}` (archived
  included).

### Topic summary

Reads include an opaque `revision`: send it in `PUT`, as
`DELETE ?revision=`, and in `/resolve` (required). Stale → `409`.
`POST /resolve` `{revision, accepted: [i], reviewed?: [i]}`: without
`reviewed` it decides the whole proposal; with it, only those hunks
(`reviewed: [i], accepted: [i]` accepts one, `accepted: []` rejects it). Hunks
carry `base_start` / `base_end` offsets into the current content and are
renumbered after each decision. `POST /items` appends a `todo` or `important`
line. Changes emit `topic-summary.changed`. Not to be confused with
`POST /api/topics/{id}/summary` (the linked GitHub issue).

## Step plans

`/api/step-plans`: `GET` (`?topic_id=` / `?chat_id=`), `GET /archived`,
`POST` (`topic_id` or `chat_id`, `message_id` or `source_text`, `propose`; a
failed proposal → `502` but the plan is kept), `GET`/`PATCH`/`DELETE /{id}`.
Actions: `/amend`, `/propose`, `/suggest-missing`, `/start`, `/launch`,
`/steps`, `/review`, `/auto/start`, `/auto/pause`, `/assess`,
`/consolidation/chat`, `/consolidate/generate`, `/final/from-message`,
`PUT /final`, `GET /final/versions`, `POST /final/revert`, `/complete`,
`/send-to-source` (alias `/send-to-topic`), `/archive`, `/unarchive`.

Steps, under `/api/steps/{id}`: `PATCH`/`DELETE`, `/move`, `/accept`,
`/status`, `PUT /artifact`, `/versions`, `/artifact/revert`,
`/artifact/generate`, `/artifact/from-message`, `/assess`, `/suggest-substeps`,
`/chat`. Generation returns `{step, outcome}` with `outcome` = `published` \|
`discussion` \| `awaiting_approval`.

| Behaviour | Detail |
| --- | --- |
| Errors | rule violations `409`; garbled model reply `502` |
| Assess | one tool-capable model review, then deterministic checks; a failed or oversized review → `502`, draft kept. Reads include `gaps` and non-blocking `observations`. |
| Auto pause | recorded immediately; in-flight work may finish; nothing later starts; filed output isn't retracted |
| Amend | `{request, steps: [{title, instruction}], expected_state_token, topic_id \| chat_id, output_instructions?}`; atomic; stale token, wrong source or live run → `409`. Marks the final stale, keeps earlier final versions. `/launch` starts a user-approved plan. |
| Final | saving or restoring a **different** final moves a completed plan back to `consolidating` (`active` if cleared) |
| Archive | refused for a live run; every write on an archived plan → `409`; archive/unarchive invalidate `state_token`s |
| Step chats | excluded from `/api/chats` |

Creating a plan with a source records a `system` message with
`kind: "step_plan"` whose `content` is a `StepPlanTraceData` JSON string,
committed with the plan and never updated:

```json
{
  "kind": "step_plan_trace",
  "version": 1,
  "plan_id": 42,
  "title": "Count from 1 to 3",
  "prompt": "Count from 1 to 3, with one number per step.",
  "source": "submitted"
}
```

`source` is `submitted` (explicit `source_text`) or `topic_message` /
`chat_message`. The MCP `get_step_plan` result includes
`final_output.filed_in_current_source`.

## Agents

An agent's URL-safe identity is its **`public_id`** (`/agents/{uuid}`,
`/agent <uuid>`, [transfer](/features/transfer)), independent of Copilot SDK
session handles, which belong to a
[run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run).
`AgentSessionRead` embeds a nullable `current_run`.

| Route | Behaviour |
| --- | --- |
| `GET /metrics`, `GET /inbox` | fleet rollup; everything waiting on you |
| `blueprints` | CRUD + `/instantiate` |
| `/{id}/start` | launch a parked agent |
| `/{id}/runs`, `/runs/{runId}` | execution history: trigger, status, capability snapshot, tokens |
| `/{id}/events` | transcript page: `events`, `cursor`, `epoch`, volatile `pending` approvals and `thinking` (`{text, active}`), `reset`, `rewindable_run_ids`. Send `?after=<cursor>&epoch=` for deltas; `reset` means replace. `?agentRunId=` narrows to one run. Every event carries `agentRunId`; archived ones a stable `link_id`. Streaming frames are never archived. |
| `GET /{id}/rewind/preview?event_id=` | dry run of a rewind: file tracking, files that would change |
| `POST /{id}/rewind` | `{event_id, mode?}`, `mode` = `conversation` (default) \| `conversation-and-files` ([restores files](/features/rewind#restoring-files)). Irreversible. `409` mid-turn, for a workflow-driven run or untracked files; `400` outside the current session; `502` if the SDK cut fails (conversation unchanged). |
| `/{id}/artifacts` | list (`?runId=`), create, `GET /{aid}`, `GET /{aid}/raw` (a `link` redirects) |
| `/{id}/state` | [scratchpad](/features/agents-mode/artifacts-state#durable-state-the-private-scratchpad): `GET`, `PUT` by key, `DELETE /state/{key}`, `DELETE /state` |
| `/{id}/triggers`, `POST /hooks/{token}` | triggers and the public webhook |
| `GET /runtime`, `POST /runtime/cli`, `POST /runtime/restart` | reachable with Agents mode **off**: capability probe, Copilot CLI download (poll `/runtime`), supervisor restart (`409` if unsupervised) |

## Workspaces

`/api/workspaces`: clones, sandboxed file tree, reads/writes,
`GET /{id}/discussions?path=` (chats/topics linking its files, `has_file`
first), `DELETE /{id}/folder?path=` (`400` root/outside, `409` for the
definitions folder). The workspace holding definition files is listed first
(`hosts_definitions: true`) and can't be deleted while it holds any.

Git, under `/{id}/git/`:

| Route | Behaviour |
| --- | --- |
| `GET status` | `branch`, `head`, `detached`, `upstream`, `ahead`/`behind`, `merging`, files with `code`, `orig_path`, `conflicted`, `browse_path` |
| `POST fetch` / `pull` / `push` / `commit` / `commit-push` | fetch updates counts only; pull fast-forwards; `commit` `{message, paths?}`; push publishes a new branch. `409` on a detached HEAD. |
| `POST discard?path=` | restore a file from HEAD |
| `GET diff?path=`, `GET file-versions?path=&original_path=&base=&head=` | diff editor sides `{original, modified, binary, too_large}` (over 2 MB: flag only). Revisions are commit ids or `HEAD` only. |
| `GET branches`, `POST switch` `{name}`, `POST branches` `{name}` (`201`) | `409` when work could be lost (named files), mid-merge, detached, or name taken; `404` unknown; `400` invalid name |
| `POST merge`, `GET conflict?path=`, `POST resolve` `{path, side?}`, `POST merge/complete`, `POST merge/abort` | merge the remote branch; `ok: false` + `status.merging` on conflicts; `resolve` keeps the saved file (`409` with markers) or `ours`/`theirs` |
| `GET log?limit=&skip=&path=`, `GET commits/{sha}` | paged history (`limit` 1–100, default 50), follows renames with `path`; commit detail with `body`, `parent`, `files` |

Paths are repo-relative; leaving the repo or touching `.git` → `400`. Names
with surrounding whitespace, control characters or ref-like prefixes
(`refs/…`, `heads/…`, `origin/…`, `HEAD`) are refused.

### Canvases

`/api/canvas` ([canvases](/features/canvas)): `GET workspace` (the built-in
Canvas workspace, created on first call), `GET /{conversation_id}` (a
conversation's canvases, newest first), `GET`/`PUT /{conversation_id}/{name}/state`
(`{state}`, the JSON a canvas page saves; `404` for an unknown canvas). An
invalid conversation id → `400`. Canvas files themselves are served at
`/raw/canvas/<conversation_id>/<file>` and written with the workspace file
routes. `GET /api/skills` items carry `builtin` (built-in skills refuse edits
and deletion with `400`).

## Live sessions

`/api/live`: [live sessions](/features/live-sessions), segments, insights,
notes, summary, attendees.

- `POST /{id}/summary` `{template?, language?}`: unknown template `400`,
  unsupported language `422`; both remembered.
- `GET /summary-templates`: templates (`source: builtin | file`), `problems`,
  `languages`, last choices, definitions `folder`/`workspace`.
  `PUT /summary-templates/selection` (`204`); `POST /summary-templates/files`
  `{template, duplicate?}` returns the file to edit.
- `POST /{id}/segments/discard` `{ids}` deletes those transcript phrases and
  returns `{deleted}`; ids from another session are ignored.
- `POST /{id}/speakers/me` `{label, me?}` marks a speaker as you.
- `POST /{id}/suggest` `{question, speaker_label?}` streams a
  [suggested answer](/features/live-sessions#suggested-answers) (`token`, then
  `done` / `none` / `error`); needs the `answers` feature (`409`).
- `GET /{id}/transcripts`, `POST /{id}/summary/from-transcript`
  `{transcript_ids?, template?, language?}` for the linked Teams meeting.

## Precursor IQ

All but `status` answer `404` when `PRECURSOR_IQ_ENABLED=false`.

| Route | Returns |
| --- | --- |
| `GET /api/iq/retrieve?q=&limit=&sections=` | ranked `hits` (citation `id`, `section`, `ref`, `url`, `snippet`, `score`, …), a grounding `markdown` block, `lexical_backend` (`fts5` \| `tsvector` \| `like`), `semantic`, `pending`. Unknown section → `422`. |
| `POST /api/iq/ask` `{question, sections?}` | cited `answer`, `model`, `citations`, `sources`; no model call when nothing matches; `502` on provider failure |
| `GET /api/iq/status` | index counts, embedding state |
| `POST /api/iq/embeddings/test` `{provider?, model?}` | `ok`, `dimensions`, `latency_ms` or `detail` |
| `POST /api/iq/reindex` | requeue everything, rebuild in background |

## Definitions

**Work in progress** ([feature](/features/definitions)).

| Route | Behaviour |
| --- | --- |
| `GET /check` | per-file and cross-file errors, warnings (unknown roles/MCP servers), `linked` rows |
| `POST /export` | write files for agents/workflows that have none (`?overwrite=true`; not in files mode); needs Agents mode |
| `GET /file-issues?workspace_id=&path=` | findings placed in the text (1-based, end-exclusive, UTF-16 columns) |
| `GET /schema/{kind}` | JSON Schema for `agent` \| `workflow` \| `summary` (from `docs/schemas/`) |
| `POST /accept` `{kind, id, content_hash}` | accept permission changes listed in `definition.review` |
| `GET /status`, `GET /migration`, `POST /migrate` `{acknowledge}`, `POST /revert`, `GET /migration/items/{kind}/{id}`, `POST /finalize` `{confirm: true}` | the [migration](/features/definitions#getting-there-from-today-s-data); `migrate` returns `ok: false` + `mismatches` without switching on failure; `finalize` is irreversible |

In files mode (`PRECURSOR_DEFINITIONS_SOURCE=files`), agent and workflow
payloads carry `definition: {state: file | invalid | none, path, message}`, and
runs carry `definition_path` / `definition_hash`.

## Plugins

| Route | Behaviour |
| --- | --- |
| `GET /api/plugins` | frontend extension descriptors |
| `GET /installed`, `PUT /installed/{id}` | installed plugins with `source`; enable/disable |
| `GET`/`PUT /installed/{id}/settings` | the plugin's opaque settings document |
| `GET /catalog` | the bundled [catalogue](/plugins), annotated with what's installed; ungated |
| `GET /versions?package=`, `GET /updates` | releases of a name or GitHub repo; newest per installed plugin (cached, `?refresh=true`) |
| `POST /install` `{package, version?}` | name, GitHub repo link or any requirement; latest = a floor at the newest release |
| `POST /installed/{id}/upgrade` `{version?}`, `DELETE /installed/{id}` | moves or removes only that plugin |
| `POST /restart` | `202` |

Install, upgrade, uninstall and restart are behind
[three gates](/reference/plugins#why-installing-is-opt-in). Install, upgrade and
uninstall answer with `restart_required` and `host_upgrade` (the nightly
Precursor moved to when its own build is no longer published, else `null`).

## Model settings

`GET`/`PUT /api/settings`:

- `model_fallbacks` (default `{"categories": []}`): ordered
  `{id, label, presets}` categories; each preset has `provider` (or `agents`),
  `model`, `reasoning_effort`, `context_tokens`, `context_tier`, with
  [defaults](/reference/configuration#llm-provider). Replaced as a whole;
  omit to keep. Invalid scope, id, effort/tier, duplicate preset, blank/`auto`
  model or out-of-range budget → `422`.
- `llm_model_category` / `agents_model_category` (`null` \| `efficiency` \|
  `balanced` \| `intelligence`): selecting one keeps the manual defaults;
  setting `llm_model` / `agents_default_model` clears it unless the category is
  sent too. Unknown or empty category → `422`. `llm_category_preset` is a
  read-only preview of the first candidate.
- Fallbacks never rewrite settings, agent definitions or workflow pins. The
  model actually used is reported in `done.model`, on the message and in usage;
  SDK sessions archive `model_selected` and `model_fallback` timeline events.

## Streamed turn events

Each `…/messages/stream` response is its own SSE stream:

| Event | Payload |
| --- | --- |
| `user_message` | the persisted prompt, with `public_id` |
| `reasoning` | `{content}`, a chunk of the model's [thinking](/features/topics#watching-the-model-think), ahead of that round's deltas |
| `delta` | answer text |
| `tool_calls` | the round's calls; `assistant_id` and `public_id` of the assistant row |
| `tool_result` | call id, name, arguments, result, error flag, and `link` `{slug, path}` when it touched a workspace file (stored on the tool message too) |
| `usage` | per metered round: `message_id`, prompt/completion/total and cache tokens |
| `mcp_auth_required` | a tool call needs an interactive sign-in (`server`, `message`, `tool`) |
| `system` | a notice, e.g. an MCP server was unavailable |
| `approval_required` | ends a turn that needs [approval](/features/approvals), instead of `done` |
| `done` | final message, `public_id`, `model` actually used |
| `suggestions` | follow-up suggestions for the answer (`message_id`, `items`) |
| `error` | the turn failed; an `Error: …` system message is also persisted |

Reasoning is stored per round on the assistant message (`reasoning`, `null`
when none) and never replayed to the model.

## Real-time events

`GET /api/events` is the SSE stream for cross-window sync. Requests send
`X-Client-Id`; a client's own events are filtered out.

| Event | Fires when |
| --- | --- |
| `topic.changed` | topic metadata or state changed |
| `chat.changed` | chat metadata changed (e.g. [auto-naming](/features/chats#chats-name-themselves); then sent to every client, including the originator) |
| `message.changed` | a message was added or edited |
| `stream.started` / `stream.ended` | a turn began / completed |
| `read.changed` | a conversation was marked read |
| `reminder.changed` | a [reminder](/features/scheduler) was created, fired or cleared |
| `agent.changed` | an [agent](/features/agents-mode)'s state or events moved |
| `meeting.changed` | a [live session](/features/live-sessions) was created, renamed, ended or deleted |
| `workflow.changed` | a [workflow](/features/workflows) step or status changed, or its definition was edited; carries run status and workflow name |
| `step-plan.changed` | a [step plan](/features/step-by-step) or step changed; carries `step_plan_id` |
| `topic-summary.changed` | a [status brief](/features/topic-summary) changed; carries `topic_id` |
| `approval.changed` / `approval.parked` | a tool approval was created, answered or parked |
| `mcp.auth_required` / `mcp.auth_url` / `mcp.auth_resolved` | an [MCP server](/features/mcp) needs, opens or completed an interactive sign-in |
| `mcp.server_state` | a server finished [warming up](/features/mcp#startup-and-the-first-prompt); carries `state` and tool count |

`agent.changed` and `read.changed` carry `agent_run_id` beside
`agent_session_id`.

### Events raised in a subprocess

Stdio MCP servers (including `precursor`) share the database but not the
in-process bus. They publish through `POST /api/events/publish`, with the
per-run token the app put in their environment, in `X-Precursor-Event-Token`
(wrong or missing → `403`). Relayed events go to every window (empty
`client_id`). Only data-refresh types are accepted (`topic.changed`,
`message.changed`, `reminder.changed`, `read.changed`, `agent.changed`,
`meeting.changed`, `workflow.changed`); anything else, such as `mcp.auth_url`,
is a `400`.
