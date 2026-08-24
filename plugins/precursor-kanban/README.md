# precursor-kanban

A GitHub **Projects v2** kanban board for [Precursor](https://github.com/lrivallain/precursor) —
and the reference implementation of a Precursor plugin.

<img src="https://lrivallain.github.io/precursor/screenshots/kanban.png" alt="The Kanban board" width="720">

## What it adds

Installing this package gives Precursor a new top-level **Kanban** section:

- a sidebar entry, home card, command-palette entry and a `/kanban` route,
- columns derived from each project's **Status** single-select field,
- drag-and-drop card moves (`updateProjectV2ItemFieldValue`),
- a card preview with the issue body, labels and comments, plus a jump to the
  linked Precursor topic.

Remove the package and the section disappears — core keeps no knowledge of it.

## Install

It ships with the host as an optional extra:

```bash
uv tool install "precursor-ai[kanban]"
# or, into an existing install
uv pip install precursor-kanban
```

Precursor discovers it at startup through the `precursor.plugins` entry-point
group; no configuration is needed.

## Requirements

- A GitHub repository configured in **Settings → GitHub**, with issue
  associations enabled — the section stays hidden otherwise.
- A token carrying the `project` scope (a superset of `read:project`), which the
  `repo` scope does **not** imply:

  ```bash
  gh auth refresh -h github.com -s project
  ```

  Without it the board returns a 403 explaining exactly this.

## Layout

| Path                       | Role                                                        |
| -------------------------- | ----------------------------------------------------------- |
| `src/precursor_kanban/plugin.py`  | `register(registry)` — mounts the router, declares the section |
| `src/precursor_kanban/router.py`  | `/api/github/projects` endpoints                      |
| `src/precursor_kanban/client.py`  | ProjectsV2 GraphQL queries (extends core's `GitHubClient`) |
| `src/precursor_kanban/schemas.py` | Board read models                                     |

The React half lives in the host repository at `frontend/src/plugins/kanban/`
and registers itself against the same `kanban` section id. See
[docs/plugins.md](../../docs/plugins.md) for the full contract.

## Development

The package is a `uv` workspace member of the host repo, so the usual commands
cover it:

```bash
uv sync                                  # installs it editable
uv run pytest plugins/precursor-kanban   # its own suite
```

## Licence

MIT, same as Precursor.
