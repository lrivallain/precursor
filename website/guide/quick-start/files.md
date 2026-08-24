---
title: Quick start — Files
---

# Quick start: Files

[Files](/features/workspaces) are git clones and folders the assistant can browse
and edit, inside a sandbox it can't escape.

## Create a workspace

Open **Files → New workspace** and choose one of two kinds:

- **Git repository** — clones it into a local working copy, so changes are
  reviewable as a `git diff` and commit-able from the UI. Needs `git` on the
  server, and uses your configured GitHub token for private repos.
- **Local folder** — creates an empty folder for authoring files, with no git
  behind it.

From there you can browse the tree, open and edit files, and review diffs.
Markdown gets a rendered preview, and `.drawio` files open in a full
[diagram editor](/features/workspaces#editing-diagrams).

## Let the assistant work on it

Enable the **`workspace-fs`** [MCP server](/features/mcp) and the assistant can
read and write those files during a normal conversation — every path confined to
the workspace root, with `.git` off limits. When it touches a file, the tool call
in the transcript carries an **Open** chip that jumps you straight to it.

Pair it with the [command runner](/features/command-runner) to run tests or a
build against what it just changed, and with the `drawio` server to author
diagrams as real, diffable files rather than images.

Full detail: [Workspaces & files](/features/workspaces).
