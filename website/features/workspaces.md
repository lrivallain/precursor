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

## Syncing with git

The bar above a git workspace shows its branch and where it stands against the
remote:

- **Behind:** opening the workspace, and **Refresh**, ask the remote for new
  commits without changing any file. `↓ 2 behind` means **Pull** has something
  to bring in. A cloud icon with a slash means the remote couldn't be reached,
  so the count may be out of date. Hover the icon for git's message.
- **Committing:** see [Reviewing and committing](#reviewing-and-committing)
  below.
- **Pushing:** commits that aren't on the remote yet show as **Push 2**.
- **New branches:** a branch that isn't on the remote yet is marked **not
  published**, and **Publish** pushes it and tracks it from then on.
- **Branch switches outside Precursor:** Pull and Push always use the branch
  that is checked out, even if you switched it in a terminal or VS Code. With no
  branch checked out (a detached HEAD), both are off.

Pull only fast-forwards. When your branch and the remote have both moved on,
Precursor stops and shows where to resolve it (`git status` in the working
copy, or **Open in VS Code**).

Paths from the app are checked before git sees them. Git's pathspec magic, such
as `:/`, is taken as a plain file name, and git never waits on a password
prompt. The token is added to each command and never written to the clone.

## Reviewing and committing

In a git workspace the left pane has two tabs: **Files**, the tree, and
**Changes**, every file that differs from the last commit. **Review & commit**
in the bar opens Changes, and the pane you last used is remembered.

<Screenshot src="/screenshots/workspaces-changes.png" alt="The Changes tab listing three changed files with checkboxes and a commit message, and a side-by-side diff of one of them" caption="Reviewing a change before committing it." />

- **See what changed:** click a file to compare the last commit with your
  working copy in the editor's diff view, **side by side** or **inline**. On a
  phone it is always inline. The diff is read-only; the file icon opens the
  file for editing. Unchanged stretches of a long file are folded away.
- **Choose what goes in:** untick the files to leave out of this commit. They
  stay changed, for a later one.
- **Commit** records the ticked files in this working copy only
  (<kbd>Cmd/Ctrl</kbd>+<kbd>Enter</kbd> in the message box). **Commit & Push**
  also sends them.
- **Discard** (the arrow on a file) puts it back as the last commit has it, and
  asks first. A **new** file doesn't exist in the last commit, so discarding it
  **deletes** it. The confirmation says so and its button reads **Delete
  file**. Discarding a rename restores the old name.

While you edit a file in a git workspace, bars in the editor's margin mark the
lines changed since the last commit: green for added, blue for modified, and a
red wedge where lines were removed. They refresh when you open or save the
file.

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
