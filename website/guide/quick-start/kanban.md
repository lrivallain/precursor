---
title: Quick start — Kanban
---

# Quick start: Kanban

[Kanban](/features/kanban) renders a GitHub **Projects v2** board inside
Precursor, so your issues are visible next to the conversations about them.

## Make the section appear

Kanban is hidden until two things are true in **Settings**:

1. a **global GitHub repo** is set — its owner's projects are what gets listed;
2. **issue associations** are enabled.

Your token also needs Projects access (the `read:project` scope, or a
fine-grained token with Projects read). Precursor resolves it from
**Settings → GitHub** or your `gh auth login` session — see
[GitHub authentication](/guide/configuration#github-authentication).

## Use the board

Open **Kanban** and pick one of your projects:

- **Columns** are the project's **Status** field options — *Todo · In Progress ·
  Done* — each with a live count.
- **Cards** are its issues, showing number, open/closed state, title and labels.
- **Moving a card** writes the new status **back to GitHub**, so the board stays
  in sync both ways.

Open a card to preview the full issue without leaving Precursor — body, labels
and comments — and post a comment or edit labels from there.

::: tip Pairs with the scheduler
Combine the board with a [scheduled topic](/features/scheduler) — a nightly
`/gh-sync` — to keep issue state fresh without polling by hand.
:::

Full detail: [Kanban board](/features/kanban).
