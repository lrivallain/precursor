---
title: Kanban board
---

# Kanban board

The **Kanban** section renders a **GitHub Projects (v2) board** right inside
Precursor — a bird's-eye view of your work in flight, grouped into columns you can
scan at a glance.

::: tip It's a plugin
Kanban is Precursor's **first official plugin**, and the reference
implementation of the [plugin contract](/features/plugins). It ships as its own
distribution — `precursor-kanban` — carrying its own API routes, schemas, tests,
**MCP tools** and even its **compiled frontend**, none of which live in core.
Install or remove the package and the whole section appears or disappears.
:::

<Screenshot src="/screenshots/kanban.png" alt="A Kanban board with Todo, In Progress and Done columns of issue cards" caption="A GitHub Project board — columns come from the project's Status field; cards are its issues, with number, open/closed state, and labels." />

## What it shows

Pick one of your GitHub **Projects v2** and Precursor draws it as a board:

- **Columns** are the project's **Status** single-select field options — e.g.
  *Todo · In Progress · Done* — each with a live item count.
- **Cards** are the project's issues, showing the issue **number**, its
  **open/closed** state, the **title**, and its **labels**.
- **Filter issues** narrows the board as you type.

## Boards beyond your own account

By default the picker lists every open project owned by the account behind your
configured repository. That is a sensible default and a poor ceiling — the board
you care about often belongs to somebody else.

**Settings → Plugins → Kanban** takes extra sources:

| You type | You get |
| --- | --- |
| `acme-corp` | Every open project that account owns |
| `acme-corp#4` | Just that one project |
| `https://github.com/orgs/acme-corp/projects/4` | Just that one project |

Extras are additive and de-duplicated, so re-listing an account you already see
changes nothing. Once boards come from more than one account the picker labels
each with its owner, which is what tells two projects called "Roadmap" apart. A
source you have lost access to is skipped rather than breaking the picker — fix
or remove it in the same panel.

You still need read access to the project, and a token with the `project` scope.

## Moving cards

Change an issue's column straight from the board and Precursor writes the new
**Status** back to the GitHub Project (`POST /api/github/projects/{id}/items/{item}/status`),
so the board stays in sync with GitHub both ways.

## Asking about your boards

The plugin also brings its own **[MCP](/features/mcp) server**, `kanban.board`,
so the assistant can read your boards in a conversation — "what's still in
Todo?" — without you leaving the topic. Three read-only tools:

| Tool | What it answers |
| --- | --- |
| `list_boards` | Which Projects v2 boards exist on the account. |
| `get_board` | Every column and card on one board. |
| `board_summary` | Counts per column, for "where does the work stand?". |

Enable it in **Settings → MCP servers**, where it appears attributed to the
plugin. The tools are deliberately read-only: moving a card is a decision, and
the board already makes it a drag.

## Previewing a card

Open a card to preview the full issue/PR without leaving Precursor: its title,
state, body, labels, and **comments**. You can edit labels and post a new comment
right from the preview.

## Enabling the board

The Kanban section is **optional**, gated twice over.

First it has to be **installed**. It rides along with the `kanban` extra, which
the recommended install already includes:

```bash
uv tool install "precursor-ai[kanban]"
# or, into an existing install
uv pip install precursor-kanban
```

A plain `pip install precursor-ai` gets a core with no board at all — no
sidebar entry, no route, no `/api/github/projects` endpoints.

Then it has to be **applicable**. Once installed, it appears — as a card on the
home launcher, an entry in the sidebar rail, and an entry in the **command
palette** (⌘K / Ctrl-K) — as soon as two conditions are met in **Settings**:

1. a **global GitHub repo** is set (its owner's projects are listed), and
2. **issue associations** are enabled.

Turn either off and the section quietly withdraws.

It also needs a GitHub token with access to your Projects (the `read:project`
scope, or a fine-grained token with Projects read). Precursor resolves the token
from **Settings → GitHub** or your `gh auth login` session — see
[Configuration](/guide/configuration#github-authentication).

::: tip Pairs with the scheduler
Combine the board with [scheduled topics](/features/scheduler) — e.g. a nightly
`/gh-sync` — to keep issue state fresh without manual polling.
:::
