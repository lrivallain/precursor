---
title: Workspaces & files
---

# Workspaces & files

A **workspace** is a git clone or a plain folder that you and the assistant can
browse and edit, from the **Files** section. The assistant's file tools are
confined to it.

<Screenshot src="/screenshots/workspaces.png" alt="A file tree on the left and a file's contents with a git diff on the right" caption="Browsing a workspace — a file tree alongside file contents and git diffs." />

## Git repositories and local folders

**New workspace** offers two kinds:

- **Git repository** — cloned into a local working copy you can pull, commit
  and push. Needs `git` on the server. The GitHub token is added to each git
  command and never written to the clone.
- **Local folder** — an empty folder with no git behind it. The same file tools
  apply.

## Syncing with git

The bar above a git workspace shows its branch and where it stands against the
remote:

| Bar shows | Means |
| --- | --- |
| `↓ 2 behind` | **Pull** has commits to bring in (checked on open and on **Refresh**, without touching files). |
| Cloud icon with a slash | The remote couldn't be reached; hover for git's message. |
| **Push 2** | Two local commits aren't on the remote. |
| **not published** | The branch isn't on the remote; **Publish** pushes and tracks it. |

- Pull and Push act on the checked-out branch, even if you switched it in a
  terminal or VS Code. With a detached HEAD both are off.
- Pull only fast-forwards. When both sides have moved, it offers to merge (see
  [Merging and conflicts](#merging-and-conflicts)).
- Committing: see [Reviewing and committing](#reviewing-and-committing).

## Branches

The branch name in the bar opens the branch picker:

- **Branches:** those in this working copy. The one checked out is ticked, and
  a branch not pushed yet is marked **not published**.
- **On the remote:** branches only the remote has. Picking one fetches it and
  creates a local branch that tracks it, even in a clone made from a single
  branch.
- **Create:** type a new name and choose **Create**. The branch starts from the
  current commit, and any uncommitted changes come along. It stays local until
  you **Publish** it. A name already taken, here or on the remote, isn't
  offered.

<Screenshot src="/screenshots/workspaces-branches.png" alt="The branch picker open under the branch name, listing local branches and branches only on the remote" caption="Switching or creating a branch." />

Switching never throws work away: it is refused with uncommitted changes (the
message names the files), during a merge, with a detached HEAD, or when the
other branch would overwrite untracked files. Precursor never forces or
stashes. Unsaved editor edits are confirmed first; the open file is then
reloaded from the new branch, or closed if it doesn't exist there.

## Merging and conflicts

When your branch and the remote have both moved on, the bar offers **Merge
remote changes** (refused while you have uncommitted changes).

- **A clean merge** records a merge commit. **Push** sends it.
- **A conflict**, where both sides changed the same lines, stops the merge. A
  **Merge in progress** bar stays up until you finish, and the **Changes** tab
  lists the conflicted files first. Committing single files is off meanwhile.

Click a conflicted file to see what happened, and your version beside the
incoming one:

- **Text conflicts:** **Resolve in the editor** opens the file with each
  conflict tinted, yours in green and theirs in blue. Above each one, choose
  **Accept current** (yours), **Accept incoming** (theirs) or **Accept both**,
  or edit the text yourself. Each choice can be undone. Save, then **Mark
  resolved**. That is refused while a conflict is left in the saved file: a
  `<<<<<<<` or `>>>>>>>` line, or a `=======` line between them. A Markdown
  heading underlined with `=======` is fine.
- **Binary files, and files one side deleted:** these can't be merged line by
  line. Choose **Keep yours** or **Keep theirs**. Keeping the side that deleted
  the file deletes it. You're asked first, because the other side's changes to
  that file are dropped.

<Screenshot src="/screenshots/workspaces-conflict.png" alt="A merge in progress: the banner with Abort and Complete merge, the conflicted file in the Changes tab, and the file in the editor with Accept current, Accept incoming and Accept both above the conflict" caption="Resolving a merge conflict in the editor." />

- **Finishing:** once no conflict is left, **Complete merge** records the merge
  commit.
- **Backing out:** **Abort merge** (after a confirmation) puts your branch and
  files back as they were before the merge.

The merge runs as `git merge` and ignores your git config (rebasing pull,
`merge.ff`, conflict style), so it behaves the same everywhere.

## Reviewing and committing

In a git workspace the left pane has three tabs:

- **Files:** the tree.
- **Changes:** every file that differs from the last commit.
- **History:** the commits on the branch (see [History](#history)).

**Review & commit** in the bar opens Changes; the last pane used is
remembered.

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

In the editor, margin bars mark lines changed since the last commit (green
added, blue modified, red wedge removed), refreshed on open and save.

## History

**History** lists the commits on the checked-out branch, newest first, with
**Load more** for older ones.

- **Opening a commit:** a commit opens to its message and the files it
  changed. Click a file to see its diff against the commit before it.
- **Merges:** a merge is marked with an icon, and its files are **compared with
  its first parent**, the branch that was merged into.
- **The first commit:** it has nothing before it, so its files show as added.
- **One file's history:** **File history** (the clock beside an open file's
  name) narrows the list to the commits that changed that file, following it
  across renames. Opening one of those commits shows that file's change
  straight away. The cross beside "History of …" goes back to the whole
  branch.

<Screenshot src="/screenshots/workspaces-history.png" alt="The History tab with a commit opened to the file it changed, and that file's diff against the previous commit" caption="What a past commit changed." />

## The Definitions workspace

Once Agents mode is on, or once you edit a
[summary template](/features/live-sessions#your-own-templates), the list starts
with a built-in **Definitions** workspace holding your
[definition files](/features/definitions) (agents, workflows, summary
templates). It can't be removed while it holds definition files, and the
assistant can only read it.

Editing help:

- **As you type:** the editor knows the file format, so it suggests keys
  (<kbd>Ctrl</kbd>+<kbd>Space</kbd>) and underlines unknown keys and wrong
  types. This works for any `*.agent.yaml`, `*.workflow.yaml` or
  `*.summary.yaml` file, in any workspace.
- **On save:** in the definitions folder, the full check runs, including
  cross-file checks such as an agent path that points nowhere or a role this
  instance doesn't have. Its errors and warnings are listed under the editor
  and underlined in the file. Click one in the list to jump to its line.

<Screenshot src="/screenshots/workspaces-definitions.png" alt="A workflow definition open in the editor, with an agent path underlined and the check below saying there is no agent file at that path" caption="The check's findings, marked in the file." />

## The sandbox

Every file operation goes through `safe_join`, which rejects paths outside the
workspace root and blocks `.git`. The same sandbox backs the workspace tools of
the `precursor` and `drawio` [MCP servers](/features/mcp). Paired with the
[command runner](/features/command-runner), the assistant can edit, test and
review a repository inside a conversation.

## Working with files

| File type | Opens as |
| --- | --- |
| Markdown | editor + rendered **Preview** |
| HTML | editor + sandboxed preview frame |
| `.drawio` | the [diagram editor](#editing-diagrams) |
| Images (PNG, JPEG, GIF, WebP, BMP, ICO, AVIF), PDF | view-only |
| Anything else | the [editor](#the-editor) |

- **Refresh file** (circular arrow by the file actions) reloads the file from
  disk, keeping edit/preview mode; it asks before dropping unsaved edits.
- **Refresh files** (circular arrow in the Files header) reloads the tree and
  git status after an assistant, command or other app changed files.
- Double-click a name to rename it. Hover a folder for **New file**, **New
  folder** and **Delete folder**.
- Deleting a folder or a whole workspace (trash icon in its header) needs you to
  type its name (a **Copy** button helps). The dialog says how many files go and
  whether they can come back (in git, committed files are restorable from
  **Changes** until you commit). Folders holding definition files can't be
  deleted; a symlinked folder loses only the link. Removing a git workspace
  deletes the local copy, never the remote.

## The editor

Files open in **Monaco**, the editor core of VS Code:

- **Highlighting and editing:** syntax highlighting, find and replace
  (<kbd>Cmd/Ctrl</kbd>+<kbd>F</kbd>), multiple cursors, folding, and the command
  palette (<kbd>F1</kbd>).
- **Saving:** <kbd>Cmd/Ctrl</kbd>+<kbd>S</kbd> saves, in the editor, in the
  diagram editor, or with the focus anywhere else on the file's page (a
  preview, the toolbar, the tree).
- **YAML and JSON:** these files are checked as you type, and a syntax error is
  underlined where it is.
- **Previews:** switching a Markdown, HTML or diagram file to **Preview** and
  back keeps your undo history, cursor and scroll position.

It follows the app theme and works on phones. Monaco is downloaded the first
time you edit a file.

<Screenshot src="/screenshots/workspaces-editor.png" alt="A Markdown file open in the Monaco editor, with line numbers and syntax highlighting" caption="Editing a file in Monaco." />

## Opening in VS Code

**Open in VS Code** beside a file's path opens that file; the one in the
workspace bar opens the working copy. Both are `vscode://file/…` links to the
server's copy, so they work only when VS Code runs on the same machine.
**Copy local path** copies the absolute path.

## Discussing a workspace

Press **Discuss** in the workspace header to open a real
[chat](/features/chats) or [topic](/features/topics) beside the files, with all
its features; a chat started here is listed in **Chats**.

<Screenshot src="/screenshots/workspaces-discussion.png" alt="A workspace with a chat open beside the file, and the picker listing discussions that attach files" caption="Pressing Discuss offers a new chat, the discussions already attaching files from this workspace, or any other chat or topic." />

The picker offers:

- **New chat** — a fresh chat about the open file.
- **Continue a discussion** — the chats and topics that already attach files
  from this workspace, those attached to the open file first. Archived chats are
  left out.
- **Open another** — search any chat or topic. One discussion is open at a
  time; **Switch** returns to the picker.

While it is open:

- The file you view, and each file you move to, is attached to the discussion
  and sent with each turn, so "this" means the open file.
- The URL carries the discussion (`/ws/<slug>/<file>?chat=<chat>` or
  `?topic=<id>`), so reloads, links and Back keep it.
- The panel title links to the full chat or topic.
- Drag the left edge to resize (320–800 px, at most 65% of the window). On
  narrow screens it is a full-width overlay; when too narrow for the composer's
  pickers it uses [compact mode](/features/chats#compact-composer).

### Artifacts

Files attached to a conversation are its **artifacts**, listed in the
**Artifacts** section of the chat or topic stats panel (count on the collapsed
rail).

- A file the assistant **creates or modifies** through the workspace tools is
  attached automatically, from a chat, a topic or the workspace panel. Reading a
  file never attaches it.
- **Attach** adds one by hand; the detach button removes the link without
  touching the file.
- Clicking an artifact opens the workspace on that file **with the
  conversation beside it**.
- Links follow the path. A file that is renamed or deleted stays listed, marked
  **missing**.

## Jumping from a conversation to the file

A tool call that reads or writes a workspace file (`drawio` or the `precursor`
workspace tools, including `read_diagram` and `read_file`) carries an **Open**
chip in the transcript. It opens the file in Files with the tree unfolded to it;
**Back** returns to the conversation. Failed calls and folders get no chip.

## Editing diagrams

A `.drawio` file (yours, or one the [`drawio` MCP server](/features/mcp)
wrote) opens in an embedded draw.io editor, with an **XML / Diagram** toggle for
the raw mxGraph source. Saving (<kbd>Cmd/Ctrl</kbd>+<kbd>S</kbd>) and `git diff`
work as for any file.

- **Theme:** follows the app (an open diagram reloads, keeping unsaved edits but
  not draw.io's undo history). The sun/moon button in the file header overrides
  it for all diagrams.
- **Self-hosted and offline:** Precursor serves draw.io at `/drawio/` with
  `offline=1&stealth=1`; diagram content never leaves your machine.
- **One-time install:** draw.io isn't in the wheel (~53 MB download). The first
  time you open a diagram, the Files pane offers to download the pinned release
  into `<data_dir>/drawio/<version>/`. `PRECURSOR_DRAWIO_VERSION` pins another
  release; `PRECURSOR_DRAWIO_DOWNLOAD_URL` uses an internal mirror (see
  [configuration](/reference/configuration)). Old versions are removed on
  upgrade.

See the [architecture reference](/reference/architecture#workspaces) for the
data model.
