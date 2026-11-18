---
title: Background app
---

# Background app

Run Precursor as a login item with a menu-bar icon and in-place updates, with no
terminal or checkout to keep around.

## One command to install

::: code-group

```bash [macOS / Linux]
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

```powershell [Windows]
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

:::

This installs the latest build, registers it to start at login, and starts it.
The wheel already carries the SPA, the in-app docs and plugin frontends, so
there is no clone, `npm` or build step. Under the hood it runs `uv tool install`
for your channel, then `precursor service install`.

For tagged releases instead of the nightly build of `main`:

::: code-group

```bash [macOS / Linux]
PRECURSOR_CHANNEL=stable sh -c "$(curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh)"
```

```powershell [Windows]
$env:PRECURSOR_CHANNEL = 'stable'
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

:::

## Managing the instance

```bash
precursor service status      # running? port, since when (--json; exits non-zero when stopped)
precursor service start       # start a detached instance (idempotent)
precursor service stop
precursor service restart     # keeps the port
precursor service logs -n 100 # tail the instance log
precursor service data-dir    # print the data directory (--reveal opens it)
precursor service install     # run at login (app + tray) and start now (--no-tray, --port N)
precursor service uninstall   # remove the login items and stop it
precursor service check       # is there a newer build?
precursor service update      # install it and restart
```

The running instance is recorded in `runtime.json` in the data directory (pid,
port, URL, version); a stale record from a crash is cleared on the next
`status`. Concurrent starts never produce two instances.

### The login items

`precursor service install` registers the app and the tray icon:

| Platform | Login item | If the app crashes |
| --- | --- | --- |
| macOS | a launchd agent in `~/Library/LaunchAgents` | launchd restarts it |
| Linux | a systemd *user* unit in `~/.config/systemd/user` | systemd restarts it |
| Windows | a `Run` entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` | it stays stopped until **Start** (tray or CLI) |

- **macOS:** re-running `install` leaves an unchanged, loaded login item alone,
  so macOS doesn't re-notify you about background items.
- **Windows:** the entry runs `precursor service start` through `pythonw` (no
  console window); it shows in Task Manager's *Startup apps*.
- Without the `tray` extra, the tray login item is skipped.

### Picking a port

If the configured port (default `8000`) is busy, `install` takes the next free
one and saves it to `.env` in the [data directory](#where-the-data-lives), so
the login item keeps using it:

```
Port 8000 is in use — Precursor will run on 8001 instead.
Port 8001 saved to /home/you/.local/share/precursor/.env
```

A port you chose (`PRECURSOR_PORT`, or `--port 8123`) is never moved: if it is
taken, the install stops with an error.

## The menu-bar icon

```bash
precursor tray
```

On Windows it sits in the notification area (pin it from the **^** overflow).
The tray needs the `tray` extra (`pystray` + `Pillow`), which the install script
includes:

```bash
uv tool install --force "precursor-ai[kanban,tray]"
```

The tray is a separate process: quitting it doesn't stop Precursor. One icon
runs per data directory.

| Icon | Means |
| --- | --- |
| **Brand colour** | running |
| **Grey** | stopped |
| **Grey, with an ellipsis** | starting, stopping or updating |

The menu starts with the instance state and the update status:

```
Precursor — running on :8000
🟢 Up to date — 2026.9.0
```

| Bullet | Status line |
| --- | --- |
| 🟢 | *Up to date* |
| 🟡 | *Update available*, with the version |
| 🔴 | *Could not check for updates* (offline, rate limited) |
| ⚪ | *Checking…*, or *Update checks are off* |

| Entry | Does |
| --- | --- |
| **Open Precursor** | opens the instance in your browser |
| **Reveal data folder in Finder**¹ | opens the [data directory](#where-the-data-lives), even while stopped (creates it if missing) |
| **Open log file** | opens the instance log (or the logs folder) |
| **Start** / **Stop** / **Restart** | the service actions |
| **Check for updates** | becomes *"Update to … and restart"* once a newer build exists |
| **Quit tray** | closes the icon only |

¹ *Show data folder in Explorer* on Windows, *Open data folder* on Linux.

If the icon is older than the running app (for example after a manual
`uv tool install --force`), the update entry becomes **"Restart the icon
(running an older build)"**.

### Being told about a new build

Configure update checks in **Settings → Updates**:

| Setting | Default | |
| --- | --- | --- |
| Update channel | Releases | *Releases* (tagged versions) or *Nightly* (see [Channels](#channels)) |
| Check for updates automatically | on | checks every half hour; when off, use *Check for updates* in the menu |
| Notify me when an update is available | off | a **system notification**, once per build (needs automatic checks on) |

The notification goes through the OS notification centre, so Focus / Do Not
Disturb apply; on macOS it is listed under *Script Editor*. The menu shows 🟡
either way, and nothing is ever installed on its own.

These preferences are stored in `update-prefs.json` in the data folder, so they
apply whether or not the app is running, and the channel also drives
`precursor service update`. Until you save a choice, `PRECURSOR_UPDATE_CHANNEL`
and `PRECURSOR_UPDATE_NOTIFY` supply the defaults.

## Updating in place

```bash
precursor service check
precursor service update
```

| Install | Detected as | Updated by |
| --- | --- | --- |
| `uv tool install precursor-ai` | `uv-tool` | reinstalling the published wheel |
| A clone run with `uv run precursor` | `source` | `git pull --ff-only` + a plugin frontend rebuild |

`GET /api/version/check` exposes the read-only check. Applying an update is
CLI/tray only.

### On Windows

Windows can't replace files a running program holds, so the update stops the
app and the icon, installs from a helper process (retrying while files are
locked), then restarts both. Expect about a minute of downtime; progress goes to
`logs\update.log`, and the returning icon reports success or the failure. A
failed install restarts the previous build.

### Channels

| Channel | Tracks |
| --- | --- |
| `stable` (*Releases*, the default) | the latest tagged release |
| `nightly` | a prerelease of `main`, published after CI passes on that commit |

Pick one in **Settings → Updates**. An empty `PRECURSOR_UPDATE_CHANNEL` follows
the running build (dev version → nightly, tagged → stable). Nightly compares
commits rather than version numbers, and installs plugin wheels built from the
same commit.

### Extras

A `uv tool` update keeps the extras already installed (read from uv's receipt).
`PRECURSOR_UPDATE_EXTRAS` adds to that list; a `-name` entry removes one.

If a **plugin** extra can't be resolved (not yet on your index, missing from a
mirror), the update retries once without it and says so:

```
Installed 2026.9.0. Skipped kanban — not installable from your index: …
```

Extras the app itself uses (`tray`, `postgres`) are never dropped. Use
`PRECURSOR_UPDATE_EXTRAS=-kanban` to stop asking for a plugin.

## Reading the log

The app writes `precursor.log` in `logs/` under the
[data directory](#where-the-data-lives), however it was started, through a
rotating handler (`PRECURSOR_LOG_FILE_MAX_BYTES`, 5 MB;
`PRECURSOR_LOG_FILE_BACKUPS`, 3).

```bash
precursor service logs -n 100   # same file as the tray's "Open log file"
```

::: details The other files in `logs/`
The others only catch what logging can't (import errors, early tracebacks,
native crashes):

| File | Written by |
| --- | --- |
| `precursor.log` | the app (rotating; `precursor.log.1`, …) |
| `tray.log` | the menu-bar icon |
| `precursor.out.log` | the raw output of a supervisor-started child, reset at each start |
| `launchd.app.err.log`, `launchd.tray.err.log` | launchd (macOS) |
| `windows.app.out.log`, `windows.tray.out.log` | the login items (Windows) |
| `update.log` | the out-of-process updater (Windows) |

On Linux, systemd captures stderr to the journal
(`journalctl --user -u precursor`).
:::

## Where the data lives

| Installed as | Database | Data directory |
| --- | --- | --- |
| A source checkout | `./precursor.db` | `./.precursor` |
| A wheel | `<user data dir>/precursor.db` | `<user data dir>` |

*User data dir* is `~/Library/Application Support/Precursor` on macOS,
`$XDG_DATA_HOME/precursor` (or `~/.local/share/precursor`) on Linux, and
`%APPDATA%\Precursor` on Windows. `PRECURSOR_DATABASE_URL` and
`PRECURSOR_DATA_DIR` override both.

A checkout keeps its own database next to its code and `precursor --dev` picks a
free port, so development never collides with the installed instance.

## Pinning the GitHub account

With several accounts in the GitHub CLI, `gh auth token` follows whichever is
active. Pick one in **Settings → GitHub → GitHub account**, or pin a default
for the login item:

```bash
PRECURSOR_GITHUB_CLI_USER=your_login
```

A token saved in **Settings → GitHub** still takes precedence.

## See also

- [Installation](/guide/installation) — all install options
- [Configuration reference](/reference/configuration) — every variable
- [Storage & retention](/features/storage) — what accumulates in the data directory
