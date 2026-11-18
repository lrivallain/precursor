---
title: Kanban board
---

# Kanban board

The **Kanban** section shows a **GitHub Projects (v2) board** inside Precursor,
lets you move cards, and lets the assistant read your boards.

::: tip It's a plugin
Kanban is Precursor's first official [plugin](/features/plugins), shipped from
[`precursor-kanban`](https://github.com/lrivallain/precursor-kanban) with its own
routes, MCP tools and frontend. It is in the
[plugin catalogue](/plugins/kanban), so **Settings → Plugins** installs it in a
click.
:::

<Screenshot src="/screenshots/kanban.png" alt="A Kanban board with Todo, In Progress and Done columns of issue cards" caption="A GitHub Project board — columns come from the project's Status field; cards are its issues, with number, open/closed state, and labels." />

## Enabling the board

The section exists only when the plugin is installed. The `kanban` extra (part
of the recommended install) brings it:

```bash
uv tool install "precursor-ai[kanban]"
```

You can also install it from **Settings → Plugins** in a running instance.

Once installed it appears on the home launcher, in the sidebar rail and in the
command palette (⌘K / Ctrl-K), and explains what is missing if GitHub isn't set
up yet.

It needs a GitHub token with Projects access (`read:project`, or a fine-grained
token with Projects read; moving cards needs write access), from **Settings →
GitHub** or `gh auth login` — see
[Configuration](/guide/configuration#github-authentication). With several
`gh` logins, the scope must be on the account picked in **Settings → GitHub**:
`gh auth refresh` only changes the active one, so run
`gh auth switch -h github.com -u <login>` first. **Issue
associations** (Settings → GitHub) is the master switch: off, the board's
endpoints return 403.

## What it shows

- **Columns** are the project's **Status** options (e.g. *Todo · In Progress ·
  Done*), with item counts.
- **Cards** are the project's issues: number, open/closed state, title, labels.
- **Filter issues** narrows the board as you type.
- **Open a card** to preview the issue/PR (title, state, body, labels,
  comments), edit labels and post a comment.
- **Moving a card** to another column writes its **Status** back to the GitHub
  Project.

## Choosing which boards appear

Boards are managed from the board picker itself; there is no Kanban settings
page.

- With a repository configured in **Settings → GitHub**, the picker lists every
  open project of the repository's owner. A repository is optional.
- The **+** next to the Precursor logo opens **Add a project**:

| You type | You get |
| --- | --- |
| `acme-corp` | Every open project that account owns |
| `acme-corp#4` | Just that one project |
| `https://github.com/orgs/acme-corp/projects/4` | Just that one project |

Duplicates are ignored. With boards from several accounts, each is labelled with
its owner.

**Right-click a board** in the picker:

- **Hide from board** removes it from the list (works on every row).
- **Stop tracking `<source>`** removes the entry that added it; for an account
  entry it says how many boards go and asks first.

Below the list:

- **Hidden (N)** lists hidden boards; right-click → **Show on board**.
- **Not resolving** lists sources that currently produce no board (renamed,
  revoked, private, no open projects); right-click → **Stop tracking**.

## Asking about your boards

The plugin adds a read-only **[MCP](/features/mcp) server**, `kanban.board`.
Enable it in **Settings → MCP servers**.

| Tool | What it answers |
| --- | --- |
| `list_boards` | Which Projects v2 boards exist on the account. |
| `get_board` | Every column and card on one board. |
| `board_summary` | Counts per column. |

::: tip Pairs with the scheduler
Combine the board with [scheduled topics](/features/scheduler) — e.g. a nightly
`/gh-sync` — to keep issue state fresh.
:::
