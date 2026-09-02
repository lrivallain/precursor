---
title: Kanban board
description: GitHub Projects v2 boards, rendered as a full section inside Precursor.
plugin: kanban
distribution: precursor-kanban
homepage: https://github.com/lrivallain/precursor-kanban
author: lrivallain
license: MIT
tags: [github, planning]
contributes: [section, settings, mcp]
recommended: true
---

# Kanban board

Renders a **GitHub Projects (v2) board** as a section of its own — columns from
the project's *Status* field, cards for its issues, drag one across and the new
status is written back to GitHub.

It is Precursor's **first official plugin** and the reference implementation of
the [plugin contract](/features/plugins): API routes, schemas, tests, MCP tools
and a compiled frontend, all shipping from
[its own repository](https://github.com/lrivallain/precursor-kanban) and none of
it in core.

## What you need

A GitHub token that can read your Projects — the `read:project` scope, or a
fine-grained token with Projects read. Precursor resolves it from
**Settings → GitHub** or your `gh auth login` session. A configured repository is
optional: it is only ever read for its *owner*, as a shortcut for "list this
account's boards".

## What it adds

| Contribution | Where it shows up |
| --- | --- |
| A **section** | Sidebar rail, home launcher, command palette, route at `/kanban` |
| An **MCP server** (`kanban.board`) | Settings → MCP servers — three read-only tools, so the assistant can answer "what's still in Todo?" |
| **API routes** | `/api/github/projects` |

## Install

It also rides along with the `kanban` extra, which the recommended install
already includes:

```bash
uv tool install "precursor-ai[kanban]"
```

The full walkthrough — adding boards, hiding them, moving cards, previewing an
issue — is in the [Kanban feature guide](/features/kanban).
