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

The token value itself is never returned by the API. When a token resolves to a
real GitHub account, the sidebar persona menu also shows your Copilot **AI
credits** and the next reset date.

### Which repository issues go to

**Settings → GitHub** also holds the default `owner/name` repository used when a
topic creates an issue. It can be overridden at two narrower levels, checked in
this order:

1. the **topic**'s own repository, set in its settings panel;
2. its [collection](/features/collections)'s repository;
3. the global setting above.

## Speech-to-text (Live sessions)

The [live meeting assistant](/features/live-sessions) transcribes audio with
**Azure AI Speech**. Set a Speech **key** and **endpoint** under
**Settings → Speech-to-text**. Until then, live sessions can be created but the
**Record** button stays disabled. Audio streams directly from the browser using a
short-lived token minted by the backend — the subscription key never reaches the
browser, and raw audio is never stored.

## Other settings areas

Precursor's **Settings** panel is organized into tabs, each covered by the
feature it configures:

| Tab | Covers |
| --- | --- |
| **Model** | Active provider + credentials, default chat model. |
| **GitHub** | Token, default repository, issue-context behaviour. |
| **MCP** | Enable [tool servers](/features/mcp), and choose which of your own sections the built-in server exposes (off by default). |
| **Collections** | Create and edit [collections](/features/collections). |
| **Agents** | Turn [Agents mode](/features/agents-mode) on/off, set the global [approval policy](/features/agents-mode/running#approval-policy-per-agent), and manage [blueprints](/features/agents-mode/orchestration#blueprints-reusable-templates). |
| **Workflows** | The [defaults a new pipeline starts from](/features/workflows/building). |
| **Live / Speech-to-text** | Enable the section, pick the fast insights model, set [transcript retention](/features/live-sessions#transcript-retention) and Azure Speech credentials. |
| **Backup** | Periodic copy of the database + attachment blobs into a plain folder. |
| **System** | Theme, [storage retention](/features/storage), and the [command-runner jail](/features/command-runner). |

Fleet-wide knobs that aren't per-object — the agent concurrency cap, retry
backoff — are [`.env` settings](#process-level-configuration-env).

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
