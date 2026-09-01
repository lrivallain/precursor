# precursor-kanban

A GitHub **Projects v2** kanban board for [Precursor](https://github.com/lrivallain/precursor) —
and the reference implementation of a Precursor plugin.

<img src="https://lrivallain.github.io/precursor/screenshots/kanban.png" alt="The Kanban board" width="720">

## What it adds

One package, three contributions:

- **A section** — sidebar entry, home card, command-palette entry and a
  `/kanban` route, with columns derived from each project's **Status**
  single-select field, drag-and-drop card moves
  (`updateProjectV2ItemFieldValue`), and a card preview showing the issue body,
  labels and comments plus a jump to the linked Precursor topic. The UI is a
  built ES module shipped **inside this wheel**, loaded by the app at runtime.
- **MCP tools** — a `kanban.board` server (`list_boards`, `get_board`,
  `board_summary`) so the assistant can read your boards mid-conversation.
- **API routes** — `/api/github/projects`, backing the board.

Remove the package and all three disappear; core keeps no knowledge of them.

## Install

It ships with the host as an optional extra:

```bash
uv tool install "precursor-ai[kanban]"
# or, into an existing install
uv pip install precursor-kanban
```

…or from **Settings → Plugins** inside the app, which also offers the restart.

Precursor discovers it at startup through the `precursor.plugins` entry-point
group; no configuration is needed.

## Requirements

- **Issue associations** enabled in **Settings → GitHub** — the master switch for
  the whole GitHub surface; the board's endpoints answer 403 without it.
- A GitHub repository is **optional**. When set it contributes its owner's
  boards, which is all it is ever read for; otherwise add projects from the
  board's **+**.
- A token carrying the `project` scope (a superset of `read:project`), which the
  `repo` scope does **not** imply:

  ```bash
  gh auth refresh -h github.com -s project
  ```

  Without it the board returns a 403 explaining exactly this.

## Layout

| Path | Role |
| --- | --- |
| `src/precursor_kanban/plugin.py` | `register(registry)` — router, section, MCP server |
| `src/precursor_kanban/router.py` | `/api/github/projects` endpoints |
| `src/precursor_kanban/client.py` | ProjectsV2 GraphQL queries (extends core's `GitHubClient`) |
| `src/precursor_kanban/schemas.py` | Board read models |
| `src/precursor_kanban/mcp_server.py` | The `kanban.board` MCP tools |
| `web/src/` | Frontend source — `index.tsx` calls `registerSection` |
| `src/precursor_kanban/web/` | **Built** frontend, shipped in the wheel (gitignored) |

See [docs/plugins.md](../../docs/plugins.md) for the full contract.

## Development

The package is a `uv` workspace member of the host repo, so the usual commands
cover it:

```bash
uv sync                                  # installs it editable
uv run pytest plugins/precursor-kanban   # its own suite
make plugins-build                       # rebuild the frontend into the package
```

The frontend builds with the *host's* Vite/React toolchain rather than its own
npm project, which guarantees it compiles against exactly the React it will
share at runtime.

## Licence

MIT, same as Precursor.
