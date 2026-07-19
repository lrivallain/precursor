---
title: Kanban board
---

# Kanban board

The **Kanban** section renders a **GitHub Projects (v2) board** right inside
Precursor — a bird's-eye view of your work in flight, grouped into columns you can
scan at a glance.

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

## Enabling the board

The Kanban section is **optional**. It appears — as a card on the home launcher, a
tab in the sidebar, and an entry in the **command palette** (⌘K / Ctrl-K) — once
two conditions are met in **Settings**:

1. a **global GitHub repo** is set (its owner's projects are listed), and
2. **issue associations** are enabled.

It also needs a GitHub token with access to your Projects (the `read:project`
scope, or a fine-grained token with Projects read). Precursor resolves the token
from **Settings → GitHub** or your `gh auth login` session — see
[Configuration](/guide/configuration#github-authentication).

::: tip Pairs with the scheduler
Combine the board with [scheduled topics](/features/scheduler) — e.g. a nightly
`/gh-sync` — to keep issue state fresh without manual polling.
:::
