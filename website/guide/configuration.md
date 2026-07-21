---
title: Configuration
---

# Configuration

Almost everything in Precursor is configured **at runtime in the app** — under
**Settings** — rather than through environment variables. Every setting has a
built-in default, so the app runs fine with **no `.env` file at all**. The
handful of process-level knobs (host, port, database URL, backup scheduling)
live in `.env`; see the [configuration reference](/reference/configuration).

## Connecting a model

Open **Settings → Model** to pick a provider and enter its credentials. Secrets
are stored in the local database and **never echoed back** by the API — only a
`*_present` boolean is returned.

| Provider | What it is | Credential |
| --- | --- | --- |
| **GitHub Copilot** *(default)* | The Copilot model catalogue (Claude, Gemini, GPT, …), OpenAI-compatible at `api.githubcopilot.com` | a `gho_*` token |
| **GitHub Models** | GitHub Models inference (`models.github.ai/inference`) | a PAT with the `models:read` permission |
| **Azure AI Foundry** | Azure OpenAI / AI Foundry deployments | endpoint + key + deployment |
| **OpenAI-compatible** | OpenAI, Mistral, Hugging Face, Ollama, or any compatible gateway | base URL + key |
| **Mock** | A deterministic streamed reply for offline development | *(none — automatic fallback)* |

::: tip Offline by default
When no credentials resolve, Precursor automatically uses the **mock provider**,
so the chat flow stays usable with zero setup. Configure a real provider whenever
you're ready.
:::

## GitHub authentication

Precursor resolves a GitHub token in this order:

1. A token saved in **Settings → GitHub**.
2. Your **GitHub CLI** session (`gh auth token`) if you're signed in via
   `gh auth login`.

So if you already use `gh`, you don't need to set anything. A token needs the
`models:read` fine-grained permission (or Copilot access) for real model
responses. With **no** token at all, Precursor falls back to the mock provider so
the chat flow stays usable offline.

The resolved source is surfaced to the UI as `settings | gh-cli | none`; the
token value itself is never returned.

## Speech-to-text (Live sessions)

The [live meeting assistant](/features/live-sessions) transcribes audio with
**Azure AI Speech**. Set a Speech **key** and **endpoint** under
**Settings → Speech-to-text**. Until then, live sessions can be created but the
**Record** button stays disabled. Audio streams directly from the browser using a
short-lived token minted by the backend — the subscription key never reaches the
browser, and raw audio is never stored.

## Other settings areas

Precursor's **Settings** panel is organized into tabs:

- **Model** — active provider + credentials, default chat model.
- **GitHub** — token, and issue-context TTL behavior.
- **MCP** — enable/disable [tool servers](/features/mcp) and toggle which of your
  own conversation sections are exposed by the built-in MCP server (`mcp_expose`,
  off by default).
- **Agents** — turn [Agents mode](/features/agents) on/off; reports whether the
  native runtime resolved on your platform.
- **Live / Speech-to-text** — enable the section, pick the fast model + reasoning
  effort for live insights, set how many days to keep a session's transcript
  (`live_transcript_retention_days`, `7` by default; `0` keeps forever), and set
  Azure Speech credentials.
- **Backup** — periodic copy of the SQLite DB + attachment blobs into a plain
  (e.g. cloud-synced) folder, with snapshot retention.
- **System** — theme (light / dark / system), storage & retention
  (`tool_result_retention_days`), and the command-runner jail.

## Process-level configuration (`.env`)

For deployment concerns — bind host, port, database URL, log level, shutdown
grace, and backup scheduling — copy `.env.example` to `.env` and uncomment what
you want to override:

```bash
# PRECURSOR_HOST=127.0.0.1
# PRECURSOR_PORT=8000
# PRECURSOR_DATABASE_URL=sqlite+aiosqlite:///./precursor.db
# PRECURSOR_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/precursor
```

See the full list in the [configuration reference](/reference/configuration).

::: warning Postgres needs an extra
The default database is a local SQLite file. To point at PostgreSQL, install the
`postgres` extra (`uv sync --extra postgres`) for the `asyncpg` driver and set
`PRECURSOR_DATABASE_URL` accordingly.
:::
