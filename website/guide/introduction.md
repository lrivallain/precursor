---
title: Introduction
---

# What is Precursor?

> **Opinionated approach to work follow-up, built as an AI assistant.**

Precursor is a local, single-user AI assistant for work that spans days or weeks:
issues, meetings and long-running tasks. Each kind of work gets its own surface,
and context stays attached to the work rather than to one conversation.

| Surface | Use it for |
| --- | --- |
| [Topics](/features/topics) | Long-lived threads, optionally linked to a GitHub issue that is re-read as context on every turn |
| [Chats](/features/chats) | Quick, throwaway questions |
| [Live sessions](/features/live-sessions) | Transcribing a meeting, with live insights and a summary |
| [Agents](/features/agents-mode) | Autonomous Copilot SDK agents for long tasks (opt-in) |
| [Workflows](/features/workflows) | Chaining agents into a reusable, schedulable pipeline with gates and approvals |
| [Files](/features/workspaces) | Git clones and folders the assistant can read and edit |

All surfaces share the same [MCP tools](/features/mcp) (Precursor is an MCP
server and client), [skills, roles and memory](/features/skills-memory), the
[scheduler](/features/scheduler) and [plugins](/features/plugins). Models come
from GitHub Copilot (default), Azure AI Foundry or any OpenAI-compatible API.
The full list is in [Feature guides](/features/).

It runs as one `uvicorn` process serving the API and the pre-built React UI
(Python 3.12+, FastAPI, SQLAlchemy, React 19; see the [stack](/reference/stack)).

::: tip Single-user by design
Precursor has **no authentication** and binds to `127.0.0.1`. Don't expose it to
a network without an authenticating reverse proxy. See the
[security model](/reference/architecture#security-deployment-model).
:::

## Next

- [Installation](/guide/installation): one command.
- [Quick start](/guide/quick-start): your first topic.
- [Configuration](/guide/configuration): GitHub, models and settings.
