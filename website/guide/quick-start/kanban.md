---
title: Quick start — Kanban
---

# Quick start: Kanban

[Kanban](/features/kanban) shows a GitHub **Projects v2** board inside Precursor.
It is a [plugin](/features/plugins).

## Install and connect

The default installer already includes it (`kanban` extra). Otherwise install it
from **Settings → Plugins** or:

```bash
uv tool install "precursor-ai[kanban]"
```

The board needs a GitHub token with Projects access (`read:project`, or a
fine-grained token with Projects read), taken from **Settings → GitHub** or
`gh auth login` ([details](/guide/configuration#github-authentication)).
**Issue associations** (Settings → GitHub) must stay on. If something is
missing, the board says what.

## Use it

- Pick a project. With a default repository set, its owner's projects are
  listed; the **+** next to the Precursor logo adds others: an account name (all
  its open projects), `owner#number` or a project URL.
- **Columns** are the project's **Status** options; **cards** are its issues.
- **Move a card** to write the new status back to GitHub.
- **Open a card** to read the issue, comment or edit labels.
- **Right-click a board** in the picker to hide it or stop tracking its source.

::: tip Pairs with the scheduler
A [scheduled topic](/features/scheduler) running `/gh-sync` nightly keeps issue
state fresh.
:::
