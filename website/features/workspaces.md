---
title: Workspaces & files
---

# Workspaces & files

A **workspace** is a git clone or a plain folder that the assistant can browse
and edit. The **Files** section lets you explore the workspaces backing your
sessions, and the assistant operates on them through a **sandboxed** file layer.

<Screenshot src="/screenshots/workspaces.png" alt="A file tree on the left and a file's contents with a git diff on the right" caption="Browsing a workspace — a file tree alongside file contents and git diffs." />

## Git repositories and local folders

**New workspace** offers two kinds:

- **Git repository** — Precursor clones it into a local working copy and can
  pull / commit, so changes are reviewable as a `git diff`. Needs `git` on the
  server; the token is injected at operation time and **never stored** on the
  workspace row.
- **Local folder** — creates an empty folder for authoring files, with no git
  behind it. The same file tools apply.

## The Agents & workflows workspace

Once Agents mode is on, the list starts with a built-in **Agents & workflows**
workspace: the folder that holds your agent and workflow
[definition files](/features/definitions) (work in progress). It can't be
removed while it holds definition files, and the assistant's file tools can
only read it.

Definition files get help while you edit them:

- **As you type:** the editor knows the file format, so it suggests keys
  (<kbd>Ctrl</kbd>+<kbd>Space</kbd>) and underlines unknown keys and wrong
  types. This works for any `*.agent.yaml` or `*.workflow.yaml` file, in any
  workspace.
- **On save:** in the definitions folder, the full check runs, including
  cross-file checks such as an agent path that points nowhere or a role this
  instance doesn't have. Its errors and warnings are listed under the editor
  and underlined in the file. Click one in the list to jump to its line.

<Screenshot src="/screenshots/workspaces-definitions.png" alt="A workflow definition open in the editor, with an agent path underlined and the check below saying there is no agent file at that path" caption="The check's findings, marked in the file." />

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

## The editor

Files open in **Monaco**, the editor core of VS Code:

- **Highlighting and editing:** syntax highlighting, find and replace
  (<kbd>Cmd/Ctrl</kbd>+<kbd>F</kbd>), multiple cursors, folding, and the command
  palette (<kbd>F1</kbd>).
- **Saving:** <kbd>Cmd/Ctrl</kbd>+<kbd>S</kbd> saves.
- **YAML and JSON:** these files are checked as you type, and a syntax error is
  underlined where it is.
- **Previews:** switching a Markdown, HTML or diagram file to **Preview** and
  back keeps your undo history, cursor and scroll position.

The editor follows the app's light or dark theme, and works on phones too.

<Screenshot src="/screenshots/workspaces-editor.png" alt="A Markdown file open in the Monaco editor, with line numbers and syntax highlighting" caption="Editing a file in Monaco." />

Monaco only downloads the first time you edit a file, so opening Precursor
stays as fast as before.

## Opening in VS Code

When a change is easier to make in a full editor, **Open in VS Code** hands it
over:

- the button beside an open file's path opens that file;
- the one in the workspace bar opens the whole working copy as a folder.

Both are `vscode://file/…` links to the **server's** copy. They only work when VS
Code runs on the same computer as Precursor, which is the usual local setup.
Changes you save there appear in the Files section and in the git status like any
other edit.

**Copy local path** gives the same absolute path, including a workspace's
subdirectory.

## The workspace assistant

The **Assistant** pane beside the editor is a conversation about the workspace,
focused on the file you have open. It is ephemeral — nothing is saved, and
**Clear** empties it (stopping a reply that is still streaming first). It uses
the same tools as a topic or a chat, shown inline, and **Stop** behaves the same
way: what already streamed is kept, and a tool call still running settles as
**stopped** rather than spinning. See
[deleting, clearing and stopping](/features/topics#deleting-clearing-and-stopping).

## Jumping from a conversation to the file

When the assistant touches a file in a workspace — a diagram via the
[`drawio` MCP server](/features/mcp), or any file via `workspace-fs` — the tool
call in the transcript carries an **Open** chip naming it. That holds in a chat,
a topic, and the workspace's own assistant alike. One click switches to the
Files section with that file already open, so a
diagram produced (or merely inspected) mid-conversation doesn't have to be
hunted down in the tree. Browser **Back** returns to the discussion.

This covers reads as well as writes: `read_diagram` and `read_file` link to what
they read, which is handy when the assistant quotes a fragment and you want the
whole thing — or when the read was truncated.

A failed call, a folder, or a path that can't be turned into a safe route
carries no link — so a chip never points somewhere unexpected.

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

See the [architecture reference](/reference/architecture#workspaces) for the
data model.
