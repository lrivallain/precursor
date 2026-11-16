---
title: Installation
---

# Installation

Install Precursor as a background app that starts at login and updates itself.
The only prerequisite is [uv](https://docs.astral.sh/uv/) (it brings its own Python).

## Install

::: code-group

```bash [macOS / Linux]
curl -LsSf https://astral.sh/uv/install.sh | sh   # skip if you have uv
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

```powershell [Windows]
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # skip if you have uv
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

:::

The script installs the **nightly** build with the Kanban and tray extras,
registers a login item and starts the app. Open <http://localhost:8000> (or the
port it reports if `8000` was taken). No clone, Node.js or database setup is
needed.

If you're signed in with `gh`, models work immediately. Otherwise the app runs
on a mock model until you [connect one](/guide/configuration#connecting-a-model).
Next: [Quick start](/guide/quick-start).

### Installer options

Set these before running the script (`VAR=value sh -c "$(curl -fsSL …)"` or
`$env:VAR = 'value'` in PowerShell):

| Variable | Default | Effect |
| --- | --- | --- |
| `PRECURSOR_CHANNEL` | `nightly` | `stable` installs the latest tagged release from PyPI. |
| `PRECURSOR_EXTRAS` | `kanban,tray` | Extras to install; `none` for the lean core. |
| `PRECURSOR_NO_START` | — | `1` installs the command without registering or starting anything. |
| `PRECURSOR_WHEEL` | — | Install this wheel (path or URL). |
| `PRECURSOR_REPO` | `lrivallain/precursor` | Repository whose nightly builds to install. |

```bash
PRECURSOR_CHANNEL=stable sh -c "$(curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh)"
```

## Manage it

```bash
precursor service status      # running? which port?
precursor service update      # newest build + restart
precursor service logs        # tail the log
precursor service restart
precursor service uninstall   # remove login items and stop
uv tool uninstall precursor-ai
```

The tray icon (`precursor tray`) does the same from the menu bar or notification
area. Channels, extras, ports and data location: [Background app](/features/background-app).

The PyPI package is **`precursor-ai`**; it installs the `precursor-ai` and
`precursor` commands. [Agents mode](/features/agents-mode) needs no extra
install: its runtime is a one-click download in **Settings → Agents**. Other
[plugins](/features/plugins) can be added at any time.

## Other ways to run it

| Goal | Command |
| --- | --- |
| Try it without installing | `uvx precursor-ai` |
| Install without a login item | `uv tool install precursor-ai`, then run `precursor` |
| Work on Precursor | a source checkout; see the [contribution guide](/contributing/) (needs Node.js) |

**As a browser app (PWA).** Chromium browsers offer *Install Precursor…* (address
bar or browser menu) for a standalone window. It has no offline cache: it only
works while the local instance is running.

## Platform notes

### Windows

- The login item is a `Run` entry (*pythonw* in Task Manager → *Startup apps*),
  not a service. **A crashed instance is not restarted**; use **Start** in the
  tray.
- `precursor service update` stops the app and the icon, updates, and restarts
  them. Progress goes to `logs\update.log` in the
  [data directory](/features/background-app#where-the-data-lives).
- **Settings → Plugins** can't install into a running instance; it shows the
  command to run between `precursor service stop` and `precursor service start`.

### Linux server (headless)

- Without `DISPLAY` / `WAYLAND_DISPLAY`, no tray icon is registered.
- The systemd *user* unit stops when you log out. To keep it running and start
  at boot:

  ```bash
  loginctl enable-linger
  ```

- Reach it through an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 you@server`, then
  open `http://127.0.0.1:8000/`. To listen on the network instead:

  ```bash
  echo 'PRECURSOR_HOST=0.0.0.0' >> "$(precursor service data-dir)/.env"
  precursor service restart
  ```

::: warning No authentication
Precursor has no login. Anyone who can reach the port can use your GitHub
credentials and everything the app can do, so only listen on a trusted network
or behind an authenticating proxy. Off loopback, the MCP HTTP endpoint and
in-app plugin installs are disabled.
:::
