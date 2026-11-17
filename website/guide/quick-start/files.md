---
title: Quick start — Files
---

# Quick start: Files

[Files](/features/workspaces) are git clones and folders the assistant can read
and edit, confined to the workspace root.

## Create a workspace

**Files → New workspace**, then either:

- **Git repository**: cloned locally (needs `git`; private repos use your
  GitHub token). Review changes as a diff and commit from the UI.
- **Local folder**: an empty folder, no git.

Browse, edit and diff files; Markdown has a preview and `.drawio` files open in
a [diagram editor](/features/workspaces#editing-diagrams).

## Let the assistant edit it

In **Settings → MCP servers**, enable the built-in **`precursor`** server's
**Workspaces** section (read) and **Edit workspaces** (write, create
workspaces). Paths stay inside the workspace and `.git` is off limits. File tool
calls in the transcript get an **Open** chip.

Add the [command runner](/features/command-runner) to run tests or builds, and
the `drawio` server to author diagrams as files.
