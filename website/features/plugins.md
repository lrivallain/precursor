---
title: Plugins
---

# Plugins

Anything beyond "topics, chat, GitHub" ships as a **plugin**: one Python
package, installed and removed independently of Precursor. The
[Kanban board](/features/kanban)
([`precursor-kanban`](https://github.com/lrivallain/precursor-kanban)) is the
reference implementation.

| A plugin can add | Shows up as |
| --- | --- |
| **A section** | sidebar rail entry, home card, ⌘K entry and top-level route |
| **MCP tools** | a server in the MCP catalogue, with the same toggles as built-ins |
| **A settings page** | an entry under **Plugins** in the Settings modal |
| **API routes** | FastAPI endpoints under `/api/<plugin>` |

Install the package and restart: everything appears. Uninstall it: everything
goes.

## Finding one

**Settings → Plugins → Available** lists the [plugin catalogue](/plugins), with
one-click **Install**.

<Screenshot src="/screenshots/plugins-catalog.png" alt="The Plugins settings panel showing the Kanban board entry with its newest release, a source picker set to GitHub, a version picker on Latest, and the resulting install command" caption="Settings → Plugins: the bundled catalogue. Each entry shows its newest release, and can install from PyPI or its GitHub repository, at any version. Installing is off here, so the entry reveals the exact command instead." />

The catalogue is **bundled with Precursor**, not fetched: it works offline and
every entry was reviewed in a pull request. A newly listed plugin arrives with
the next release. Entries can only name a bare PyPI project (and optionally its
GitHub repository); installing one uses the same gated endpoint as typing the
name. Wrote a plugin? [Get it listed](/plugins/submitting).

## Installing one

**Settings → Plugins** lists installed plugins, what each contributes and any
load error, and lets you install, toggle, uninstall and restart.

<Screenshot src="/screenshots/plugins.png" alt="The Plugins settings panel listing precursor-kanban 2026.9.1, installed from its GitHub repository, with 2026.9.2 available and the command to upgrade it" caption="Settings → Plugins: each installed package with where it came from, whether a newer release exists there, and what it contributes." />

The install box accepts:

- a **package name** (from your package index);
- a **GitHub repository link** (`https://github.com/owner/repo`), installed from
  the wheel attached to a release (the release must carry a built wheel; a
  source checkout has no UI);
- any other requirement your installer accepts.

For a name or repository, the newest release is looked up as you type and a
**version picker** offers the others. From a terminal, install into
Precursor's own environment:

```bash
uv pip install 'precursor-kanban>=2026.9.2'
```

The panel shows the command that matches *your* install: a `uv tool install`
environment can't be extended with `pip install`.

| | |
| --- | --- |
| **Restart** | needed after install (entry points and routes are bound at startup); the panel offers **Restart now** |
| **Disable** | instant: section, routes and MCP servers go; the MCP toggle state is remembered |
| **Opt-in** | the in-app installer is **off by default**, only answers requests addressed to Precursor's loopback address, and is switched on at the top of the panel. Listing, toggling and running the command yourself are always available. |
| **Windows** | Precursor can't reinstall itself while running: the panel shows the command; run `precursor service stop`, the command, then `precursor service start` |

## Versions and upgrades

Plugins version **independently of Precursor** and are checked against the
source they came from (PyPI or GitHub releases).

- **Latest** installs a floor (`precursor-kanban>=2026.9.2`): if your index
  lags or the release doesn't fit this Precursor, the install **fails** instead
  of settling for an older one.
- **A chosen version is pinned** (`==2026.9.1`) through other upgrades and
  `precursor service update`, until you choose **Latest** or another version.
- **Installed** shows when a newer release exists (**Check for updates**), with
  **Upgrade** and **Other version** (older ones too).
- **An upgrade moves only that plugin.** With `uv pip` / `pip`, Precursor's own
  requirements are passed as constraints, so a plugin can't downgrade core's
  dependencies.

A plugin installed from a path or arbitrary URL shows as *local*; reinstall it
to upgrade.

## Writing one

A plugin registers through the `precursor.plugins` entry point, imports only
from `precursor.plugin_api`, and ships its built frontend in its wheel. The full
contract is in the [plugin reference](/reference/plugins).
