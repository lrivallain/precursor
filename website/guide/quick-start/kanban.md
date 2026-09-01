---
title: Quick start — Kanban
---

# Quick start: Kanban

[Kanban](/features/kanban) renders a GitHub **Projects v2** board inside
Precursor, so your issues are visible next to the conversations about them. It
ships as a [plugin](/features/plugins), so it is installed rather than built in.

## Make the section appear

First it has to be **installed** — it rides along with the `kanban` extra, which
the recommended install already includes:

```bash
uv tool install "precursor-ai[kanban]"
# or, into an existing install
uv pip install precursor-kanban
```

Then two things have to be true in **Settings**:

1. a **global GitHub repo** is set — its owner's projects are the default listing;
2. **issue associations** are enabled.

Turn either off and the section withdraws. **Settings → Plugins** says which one
is missing if it doesn't appear.

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

## Boards from other accounts

With a repository configured, the picker lists the projects its account owns.
That is a convenience, not a requirement — the repo is only read for its owner.
To track anything else, or to run with no repo at all, hit the **+** next to the
Precursor logo: an account name adds every open project it owns, `owner#number`
or a project URL adds just that one.

**Right-click a board** in the picker to hide it, or to stop tracking the source
that added it.

::: tip Pairs with the scheduler
Combine the board with a [scheduled topic](/features/scheduler) — a nightly
`/gh-sync` — to keep issue state fresh without polling by hand.
:::

Full detail: [Kanban board](/features/kanban).
