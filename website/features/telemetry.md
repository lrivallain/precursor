---
title: Anonymous usage data
---

# Anonymous usage data

Tell the maintainers how many installs there are, which versions they run and
which features get used, without sending anything about you or your work.
It is **off until you say yes**.

## Turning it on (or not)

The first time you open Precursor, a banner asks whether to share anonymous
usage data. **See what is sent** shows the exact JSON body before you decide.
Either answer is saved and the banner does not come back. Change it later in
**Settings → Privacy**, which also shows today's heartbeat.

Two switches, both off by default:

| Switch | What it sends |
| --- | --- |
| **Send a daily heartbeat** | Once a day: version, update channel, install type (`uv-tool`, `wheel`, `source`), OS and CPU architecture, Python version, the LLM provider id, which features are on (Agents, Live, Precursor IQ, backup), how many MCP servers and plugins, and usage counts in rough buckets (`0`, `1-9`, `10-49`, `50-199`, `200+`): messages and agent runs in the last 24 hours, topics, chats. |
| **Send error reports** | Needs the heartbeat. When Precursor logs an error: the exception type, the logger name, and the frames from Precursor's own code (file relative to the package, function, line). At most one report per distinct error a day, and 20 a day in all. |

A random install id, minted when you turn the heartbeat on, lets installs be
counted once. Turning the heartbeat off deletes it: turning it back on later
looks like a new install.

## What is never sent

Conversations, prompts, replies, file names or paths, workspace or repository
names, MCP server names, user or host names, accounts, tokens, and **error
messages** (they routinely quote your data). Counts are bucketed, never exact.

## Where it goes

Straight to the project's Azure Application Insights resource over HTTPS, as a
small JSON body. Precursor does not use the Application Insights SDK, so no
request, SQL or log line is collected on the side. Application Insights uses
the connection's IP address to derive a country and city and does not store
the address itself.

## Turning it off for good

Any of these sends nothing, whatever was chosen in the app:

- `PRECURSOR_TELEMETRY=off` in the [`.env`](/guide/configuration#process-level-configuration-env),
- `DO_NOT_TRACK=1` in the environment,
- `CI=true` (CI runs are never counted),
- a build or fork with an empty `PRECURSOR_TELEMETRY_CONNECTION_STRING`.

**Settings → Privacy** then says why nothing is sent.
