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

The same sandbox backs the **`workspace-fs`** and **`drawio`**
[MCP servers](/features/mcp), so when the assistant reads, edits or diagrams
files during a turn, it stays inside the jail.

## Working with files

From the Files section you can browse the tree, open files, and view **git
diffs** for changes. Combined with the [command runner](/features/command-runner)
and the `workspace-fs` MCP tools, this lets the assistant make and review changes
to a repository as part of a conversation — while everything stays confined to the
workspace root.

Markdown files get a rendered **Preview**, HTML files render in a sandboxed
frame, and `.drawio` files open in a full diagram editor (below).

## Jumping from a conversation to the file

When the assistant touches a file in a workspace during a chat or topic — a
diagram via the [`drawio` MCP server](/features/mcp), or any file via
`workspace-fs` — the tool call in the transcript carries an **Open** chip naming
it. One click switches to the Files section with that file already open, so a
diagram produced (or merely inspected) mid-conversation doesn't have to be
hunted down in the tree. Browser **Back** returns to the discussion.

This covers reads as well as writes: `read_diagram` and `read_file` link to what
they read, which is handy when the assistant quotes a fragment and you want the
whole thing — or when the read was truncated.

Mechanically, the tools annotate their result with `workspace_slug` and a `url`
(`services/mcp/workspace_links.py`); the SPA renders a chip whenever a tool
result carries one. A failed call, a folder, or a path that can't be turned into
a safe route carries no link — so a chip never points somewhere unexpected.

## Editing diagrams

A `.drawio` file — whether you drew it yourself or the assistant authored it
with the [`drawio` MCP server](/features/mcp) — opens in an embedded draw.io
editor, with an **XML / Diagram** toggle to drop down to the raw mxGraph source.
Edits stream back into the same buffer as any other file, so the usual dirty
marker, **Save**, and `git diff` apply unchanged.

The editor is **self-hosted**: Precursor serves its own copy of the draw.io
webapp at `/drawio/`, and the frame runs with `offline=1&stealth=1`, so diagram
content never reaches `diagrams.net` or any other external origin — and editing
keeps working with no network at all.

That copy is **not** bundled in the wheel (the release is ~53 MB, ~150 MB
extracted). The first time you open a diagram, the Files pane offers a one-time
install that downloads the pinned release into `<data_dir>/drawio/<version>/`.
Set `PRECURSOR_DRAWIO_VERSION` to pin a different release, or
`PRECURSOR_DRAWIO_DOWNLOAD_URL` to fetch it from an internal mirror — see
[configuration](/reference/configuration). Superseded versions are removed when
a new one installs.

## Data model

Each workspace is a `Workspace` row. Git operations live in
`services/workspace_git.py`; sandboxed file operations in
`services/workspace_fs.py`. See the
[architecture reference](/reference/architecture#workspaces).
