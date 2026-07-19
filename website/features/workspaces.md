---
title: Workspaces & files
---

# Workspaces & files

A **workspace** is a git clone or a local directory that the assistant can browse
and edit. The **Files** section lets you explore the workspaces backing your
sessions, and the assistant operates on them through a **sandboxed** file layer.

<Screenshot src="/screenshots/workspaces.png" alt="A file tree on the left and a file's contents with a git diff on the right" caption="Browsing a workspace — a file tree alongside file contents and git diffs." />

## Git clones and local directories

- **Clone a repo** — Precursor clones and can pull / commit. The token is
  injected at operation time and **never stored** on the workspace row.
- **Point at a local directory** — work against files already on disk.

## The sandbox

Every file operation is routed through `safe_join`, which:

- rejects path traversal outside the workspace root, and
- blocks access to `.git`.

The same sandbox backs the **`workspace-fs`** [MCP server](/features/mcp), so
when the assistant reads or edits files during a turn, it stays inside the jail.

## Working with files

From the Files section you can browse the tree, open files, and view **git
diffs** for changes. Combined with the [command runner](/features/command-runner)
and the `workspace-fs` MCP tools, this lets the assistant make and review changes
to a repository as part of a conversation — while everything stays confined to the
workspace root.

## Data model

Each workspace is a `Workspace` row. Git operations live in
`services/workspace_git.py`; sandboxed file operations in
`services/workspace_fs.py`. See the
[architecture reference](/reference/architecture#workspaces).
