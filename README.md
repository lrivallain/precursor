# Precursor

> Opinionated approach to work follow-up, built as an AI assistant.

Precursor is a local-first, single-user app that keeps your work in one place
and puts AI to work on it: long-lived **topics** (optionally linked to GitHub
issues), quick **chats**, **live meeting** transcription and insights,
**autonomous agents** and **workflows** that run in the background, file
**workspaces**, and **MCP** tools in both directions. It runs as one Python
process on your machine, with the model provider of your choice.

📖 **Documentation: <https://precursor.vuptime.io/>**

## Quick start

[uv](https://docs.astral.sh/uv/getting-started/installation/) is the only
prerequisite (it brings its own Python):

```bash
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

This installs Precursor, starts it at login, and starts it now. Open the URL it
prints (<http://localhost:8000> by default). No clone, Node.js or database setup
needed. To just try it: `uvx precursor-ai`.

```bash
precursor service status    # is it up, and on which port
precursor service update    # newest build + restart
precursor tray              # menu-bar control
```

**Models.** If you are signed in with the GitHub CLI (`gh auth login`) and have
Copilot access, GitHub Copilot works with no setup; otherwise add a token or
another provider in **Settings**. Without any provider, a mock model keeps the app usable offline.
See [Installation](https://precursor.vuptime.io/guide/installation) and
[Configuration](https://precursor.vuptime.io/guide/configuration).

## What's inside

| Area | What you get |
| --- | --- |
| **Conversations** | Tree-organized [topics](https://precursor.vuptime.io/features/topics) with editable summaries and GitHub issue context, quick [chats](https://precursor.vuptime.io/features/chats), [step-by-step](https://precursor.vuptime.io/features/step-by-step) plans, attachments, context compression, timeline & rewind |
| **Meetings** | [Live sessions](https://precursor.vuptime.io/features/live-sessions): Azure Speech transcription with speaker labels, live insights, suggested answers, notes and a templated summary |
| **Automation** | [Agents](https://precursor.vuptime.io/features/agents-mode) (Copilot SDK) followed from a control tower, multi-agent [workflows](https://precursor.vuptime.io/features/workflows) with gates, approvals and triggers, a [scheduler](https://precursor.vuptime.io/features/scheduler) and reminders |
| **Files & tools** | [Workspaces](https://precursor.vuptime.io/features/workspaces) (git clones or folders, sandboxed), [MCP](https://precursor.vuptime.io/features/mcp) server and client with tools loaded on demand, [tool approvals](https://precursor.vuptime.io/features/approvals), a jailed command runner, skills, roles and memory |
| **Models** | GitHub Copilot, Azure AI Foundry (incl. Claude with prompt caching), OpenAI, Mistral, Hugging Face, Ollama; per-conversation choice; an opt-in [OpenAI-compatible endpoint](https://precursor.vuptime.io/features/openai-endpoint) |
| **Find & organize** | [Precursor IQ](https://precursor.vuptime.io/features/iq) cited answers over your own data, collections, a [Kanban](https://precursor.vuptime.io/features/kanban) board (plugin), import/export |
| **Platform** | [Background app](https://precursor.vuptime.io/features/background-app) with tray and self-update, phone layout, keyboard shortcuts, [plugins](https://precursor.vuptime.io/features/plugins) |

Stack: Python 3.12+, FastAPI, SQLAlchemy 2 (async) + Alembic, SQLite or
PostgreSQL; React 19 + TypeScript + Tailwind, built and served by the backend.

## Security model

> [!IMPORTANT]
> Precursor is **single-user and has no authentication**. It binds to
> `127.0.0.1` by default; do not expose it to a network without an
> authenticating reverse proxy in front.

- Anyone who can reach the port has full access to your data and stored tokens.
- Keep the command runner's Docker jail on; disabling it grants local disk access.
- Secrets live in the local database and are never echoed by the API (except the
  opt-in OpenAI endpoint's own key, shown so you can copy it).

Report vulnerabilities as described in [SECURITY.md](https://github.com/lrivallain/precursor/blob/main/SECURITY.md).

## Contributing

```bash
git clone https://github.com/lrivallain/precursor.git && cd precursor
make sync                     # Python deps (uv) + frontend deps (needs Node.js)
cp .env.example .env
uv run precursor --dev        # backend reload + Vite HMR
make check                    # the full quality gate
```

Read [CONTRIBUTING.md](https://github.com/lrivallain/precursor/blob/main/CONTRIBUTING.md) and [ARCHITECTURE.md](https://github.com/lrivallain/precursor/blob/main/ARCHITECTURE.md)
(where code goes, building blocks to reuse, quality limits) before opening a PR.

## Releases

CalVer (`YYYY.M.MICRO`) from git tags. Green changes on `main` ship nightly to
[GitHub Releases](https://github.com/lrivallain/precursor/releases) and
[PyPI](https://pypi.org/project/precursor-ai/) (`precursor-ai`); a rolling
`nightly` prerelease follows `main`. See [RELEASING.md](https://github.com/lrivallain/precursor/blob/main/RELEASING.md) and
[CHANGELOG.md](https://github.com/lrivallain/precursor/blob/main/CHANGELOG.md).

## License

[MIT](https://github.com/lrivallain/precursor/blob/main/LICENSE)
