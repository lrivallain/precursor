---
title: MCP reference
---

# MCP reference

Behaviour details of Precursor's MCP client and server, for debugging and for
hosts that integrate with it. The user guide is [MCP](/features/mcp).

## Model-facing tool names

Server/tool pairs are mapped to names of ASCII letters, digits, `_` and `-`,
at most 64 characters, so every provider accepts them. A name that needed
conversion gets a hash suffix to keep it unique. Calls still reach the original
server and tool; server ids and saved toggles are unchanged.

## Stored catalogue and warm-up

- Each server's tool list is written after a successful connect and restored on
  launch. It is a hint: a call to a tool the server no longer exposes re-lists
  the catalogue and retries once.
- Warm-up connects enabled servers sequentially after
  `PRECURSOR_MCP_WARMUP_DELAY_SECONDS`, with `PRECURSOR_MCP_WARMUP_GAP_SECONDS`
  between them, never blocking startup or a request. Servers whose credential
  needs an interactive sign-in are skipped.
- Idle client sessions are closed after `PRECURSOR_MCP_IDLE_TTL_SECONDS`.

## Remote failures

The MCP SDK reports a forgotten remote session as `Session terminated` (only
emitted on HTTP 404); other servers say `Session not found`, and other HTTP
errors arrive without their status. Precursor re-attaches the status to the
message.

A remote 404, any 5xx, or a socket teardown is treated as a **transport**
failure: the warm session is recycled and the call retried once. A second
failure is returned to the model as an outage, stating that arguments and
sign-in are not at fault, so it reports the problem instead of re-planning or
asking you to sign in again.

## WorkIQ and Agent 365 credentials

Two Entra credentials are in play: the hosted WorkIQ preview, and one shared by
the five Agent 365 servers (same client and resource).

- **Lazy sign-in.** A turn never waits for a credential it doesn't use. A
  blocked server still contributes its tools from the stored catalogue (hiding
  them makes models answer from memory); a call to one asks for sign-in, naming
  the tool, and fails explicitly if it doesn't happen.
- **Issuer and consent.** Both endpoints use Entra's multi-tenant
  `organizations` authority, which publishes a templated issuer
  (`…/{tenantid}/v2.0`). Precursor accepts exactly that template for those
  authorities (and a concrete tenant issuer on the redirect). It drops the
  `prompt=consent` the SDK adds when asking for a refresh token, which would
  otherwise force a consent screen and block tenants that reserve consent for
  admins.
- **Real expiry.** A stored token's expiry is restored on load so the SDK's
  silent refresh runs. The authorization server is resolved before refreshing,
  so the grant is posted to Entra, not to the WorkIQ / Agent 365 host.
- **Scopes on refresh.** WorkIQ preview refreshes reuse the stored token's
  granted scope (omitting it can produce `AADSTS530084`). Agent 365 refreshes
  send **no** scope, because their granted list includes entries Entra refuses
  back (`AADSTS70011`). Legacy tokens without a stored scope use the SDK default.
  No extra permissions are requested and Conditional Access is not bypassed.
- **Keep-alive.** A ticker (`PRECURSOR_WORKIQ_KEEPALIVE_POLL_SECONDS`) renews a
  token a quarter of its lifetime before expiry (at least five minutes; ~17–22
  minutes for a typical Entra token). A deliberate renewal marks the credential
  spent so the SDK really refreshes, and a renewal that changed nothing is
  logged as an anomaly.
- **Idle credentials.** Not called for
  `PRECURSOR_WORKIQ_KEEPALIVE_IDLE_AFTER_SECONDS` (6 h), a credential stops being
  refreshed. Once it has really expired it is probed once, and a dead refresh
  token raises the banner (`PRECURSOR_WORKIQ_KEEPALIVE_SURFACE_IDLE_LAPSE`).
- **Hands-free re-auth.** A lapsed credential is first renewed through a silent
  browser pass. A failed pass restores the previous token, so a transient 401
  doesn't throw away a working refresh token. An explicit **Sign in** clears it.
  After one sign-in succeeds, the other credential is retried silently while
  the SSO session is hot (`PRECURSOR_WORKIQ_CHAIN_REAUTH_ENABLED`).
- **Shared sign-in.** When one Agent 365 server signs in, the others are
  re-pointed at the fresh token and reconnected.
- **Agents and workflows.** Attaching WorkIQ uses the normal expiry check, not a
  forced refresh, so a still-valid token keeps working.
- **Old tokens.** A token stored without a refresh token can't renew; Precursor
  raises the sign-in banner once.

All knobs: [configuration](/reference/configuration#mcp-tool-servers).

## Auth diagnostics

Every decision about a WorkIQ credential is logged on the
**`precursor.mcp.auth`** logger, prefixed `[workiq-auth]` and tagged with an
episode id that ties a keep-alive verdict, a hands-free pass and a manual click
together:

```text
2026-08-24T09:11:02Z INFO precursor.mcp.auth [workiq-auth] [wq-3f2a1c] workiq +0ms — episode opened reason='keep-alive could not renew silently'
2026-08-24T09:11:02Z WARN precursor.mcp.auth [workiq-auth] [wq-3f2a1c] workiq +38ms — silent refresh REFUSED by Entra error='invalid_grant' error_description='AADSTS700082: …'
2026-08-24T09:11:03Z INFO precursor.mcp.auth [workiq-auth] [wq-3f2a1c] workiq +1204ms — leg ① starting: silent prompt=none authorization
2026-08-24T09:11:23Z INFO precursor.mcp.auth [workiq-auth] [wq-3f2a1c] workiq +21208ms — loopback timed out — no redirect ever arrived
```

- Level: `PRECURSOR_WORKIQ_AUTH_LOG_LEVEL` (default `debug`, independent of
  `PRECURSOR_LOG_LEVEL`). The channel is quiet outside an auth episode; use
  `info` for transitions only or `warning` to silence it.
- `GET /api/mcp/auth/diagnostics` serves the in-memory trace plus, per
  credential: token stored, refresh token present, expiry, renewal lead
  (`renewal_lead_seconds`), idle time, and whether connects are fast-failed.
  Episode records are buffered apart from the keep-alive heartbeat, and the
  ticker only logs verdict changes, so an overnight lapse is still there in the
  morning.
- In the browser console, `await precursorWorkiqAuthReport()` copies the SPA's
  `[workiq-auth]` trace and the backend diagnostics to the clipboard. If
  DevTools had focus, it prints the JSON instead (or use
  `copy(await precursorWorkiqAuthReport())`).

Token values never leave the process: secrets and account names appear as
`<present:N chars>`, and the Agent 365 scope list is summarised to a count plus
whether `offline_access` is in it.

## The `precursor` server

- **Message permalinks.** `list_messages` and `list_chat_messages` return an
  instance-relative `permalink` and the immutable `public_id` for each
  user/assistant message, so a model can cite `[Previous reply](permalink)`.
  System notices and tool results have none.
- **Topic paths.** Every topic payload carries `path`, the ancestor slugs joined
  root-first with `/`.
- **`append_note` vs `post_message`.** `append_note` stores the text and
  returns; `post_message` spends a generation replying. Both return a `topic`
  object with title, slug path and in-app URL.
- **Search** uses the ⌘K palette's substring lookup. Chat, agent and live hits
  only appear when their own section is exposed.
- **Side chats.** `create_side_chat` generates nothing: it creates an empty chat
  (optionally from a `message_id` or `quote`) and returns its URL. `get_topic`
  lists side chats only when `chats` is exposed too.
- **Step plans.** A run happens inside the app and follows the source
  conversation's autonomy: autonomous closes the plan itself, balanced asks for
  one final review, plan only drafts it, manual asks at every step. The model is
  woken once the output is filed, and the source conversation shows a live task
  card. For calls made by the app in the same conversation, the source is filled
  in when omitted. A follow-up can amend a related plan, keeping prior work.
  `get_step_plan` reports whether a recorded copy of the current final still
  exists in the source. Archived plans are excluded from listings and must be
  restored before being amended or run.
- **Live updates from stdio.** The stdio server shares the database but not the
  app's event bus. Processes Precursor launches (the built-in entry, the one
  given to an agent) receive a loopback relay URL and a per-run token and
  forward data-refresh events, so open windows update. The relay accepts only
  those event types, with the token. An externally launched stdio server has no
  token; use the HTTP endpoint, which runs in-process.
- **Migration.** The former `workspace-fs` server is folded into `precursor`;
  existing enablements and `workspace-fs` agent scopes migrate automatically.
- Saving the exposure toggles reloads the server's tool list, so the tool count
  on its Settings card follows them.

## Agent sessions and HTTP servers

HTTP servers with no custom headers are attached to agent sessions with an empty
headers object; authenticated servers keep their configured headers. Entra
(Azure CLI) servers get their token as a header and the agent session is rebuilt
before it expires.
