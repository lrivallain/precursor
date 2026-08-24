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
distribution — `precursor-kanban` — with its own routes, schemas, tests and
release cadence, rather than living in core. Install or remove it and the
section appears or disappears wholesale.
:::

<Screenshot src="/screenshots/kanban.png" alt="A Kanban board with Todo, In Progress and Done columns of issue cards" caption="A GitHub Project board — columns come from the project's Status field; cards are its issues, with number, open/closed state, and labels." />

## What it shows

Pick one of your GitHub **Projects v2** and Precursor draws it as a board:

- **Columns** are the project's **Status** single-select field options — e.g.
  *Todo · In Progress · Done* — each with a live item count.
- **Cards** are the project's issues, showing the issue **number**, its
  **open/closed** state, the **title**, and its **labels**.
- **Filter issues** narrows the board as you type.

## Moving cards

Change an issue's column straight from the board and Precursor writes the new
**Status** back to the GitHub Project (`POST /api/github/projects/{id}/items/{item}/status`),
so the board stays in sync with GitHub both ways.

## Previewing a card

Open a card to preview the full issue/PR without leaving Precursor: its title,
state, body, labels, and **comments**. Each comment shows its author and the
**date and time it was posted** (with an *(edited)* hint when it was changed
afterwards). You can edit labels and post a new comment right from the preview.

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
