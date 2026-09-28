---
title: Installation
---

# Installation

One prerequisite, one command. At the end Precursor is running, comes back after
every reboot, and updates itself — nothing to clone, build or configure.

## 1. Install uv

[uv](https://docs.astral.sh/uv/) is the **only** prerequisite. It brings its own
Python, so there is nothing else to line up first.

::: code-group

```bash [macOS / Linux]
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell [Windows]
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

:::

Already have it? Skip ahead. Other ways to get uv — Homebrew, winget, pipx — are
in the [uv install guide](https://docs.astral.sh/uv/getting-started/installation/).

## 2. Install Precursor

::: code-group

```bash [macOS / Linux]
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

```powershell [Windows]
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

:::

That is the whole installation. **No clone, no Node.js, no build step, no
database to create** — the published package already carries the interface, and
the schema is created the first time the app starts.

The script installs Precursor, registers it to **start when you log in**, and
starts it now:

```
Precursor is installed and will start when you log in.
```

Open <http://localhost:8000> — or whichever port it reported, if `8000` was
already taken — and you're in.

::: tip Signed in with the GitHub CLI?
Then you're done: Precursor reuses your `gh` session for model access. If not, it
starts on a built-in mock model so the app is usable straight away, and
[Configuration](/guide/configuration) shows how to connect a real one.
:::

## 3. Start using it

- **[Quick start](/guide/quick-start)** — your first topic, in a couple of minutes.
- **[Configuration](/guide/configuration)** — connect GitHub and a real model.

## Living with it

Precursor is *managed* rather than launched, so there is no terminal window to
keep open:

```bash
precursor service status    # is it up, and on which port
precursor service update    # newest build + restart
precursor service logs      # tail the instance log
```

The menu-bar icon (`precursor tray`; the notification area on Windows) does the
same things with the mouse and shows at a glance whether the app is up. See
[Background app](/features/background-app) for the whole surface — the login
item, update channels, and where your data lives.

To remove it:

```bash
precursor service uninstall   # drop the login items and stop it
uv tool uninstall precursor-ai
```

## On Windows

The PowerShell installer gives Windows the same result as the shell one — the
same build, the same extras, a login item and the notification-area icon — and
every `precursor service …` command works the same way. What differs is what
Windows itself does differently:

- **The login item is a `Run` entry**, not a service. It shows in Task Manager's
  *Startup apps* (as *pythonw*, the windowless Python that runs it), where you
  can switch it off like any other. It starts Precursor at login and steps
  aside — no console window stays open.
- **Nothing restarts a crashed instance.** macOS and Linux hand the process to a
  service manager that brings it back; Windows has none for a per-user app. The
  tray shows the instance as stopped, and **Start** brings it back.
- **Updating stops Precursor for a minute.** Windows can't replace files that a
  running program holds open, so `precursor service update` (or the tray's
  update entry) stops the app and the icon, installs the new build, and starts
  both again. Progress goes to `logs\update.log` in the
  [data directory](/features/background-app#where-the-data-lives), and the icon
  says how it went when it comes back.
- **Plugins install from a terminal.** For the same reason, *Settings → Plugins*
  can't reinstall a running Precursor. It shows the command to run while
  Precursor is stopped: `precursor service stop`, that command, then
  `precursor service start`.

## Other ways to install

The command above is the supported path; use it unless one of these reasons
applies to you.

### Just to try it out

Runs the latest published build without installing anything and leaves nothing
behind:

```bash
uvx precursor-ai
```

### Without a login item

Installs the command but registers no autostart, so you decide when it runs:

```bash
uv tool install precursor-ai
precursor                     # run it in the foreground
```

### Tagged releases instead of nightly

The script installs the **nightly** build — a rolling prerelease of `main`. For
tagged releases only:

::: code-group

```bash [macOS / Linux]
PRECURSOR_CHANNEL=stable sh -c "$(curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh)"
```

```powershell [Windows]
$env:PRECURSOR_CHANNEL = 'stable'
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

:::

The same choice is available afterwards from the tray and from
`precursor service update`.

### From source, to work on Precursor

A source checkout is for **contributing**, not for using the app: it additionally
needs Node.js for the frontend toolchain, and takes more steps to reach the same
result. The [contribution guide](/contributing/) covers the dev stack.

### As a browser app (PWA)

Precursor ships a web app manifest, so Chromium browsers offer to **install it as
a standalone app** — its own window, a dock/taskbar icon, no address bar. Look
for the install icon in the address bar, or the browser menu →
*Install Precursor…*.

::: warning It's a window, not an offline app
The installed app is a convenience wrapper around your **local** Precursor
instance — there is deliberately **no offline caching**. It only works while the
instance is running, on the same machine, over `localhost` (which counts as a
secure context, so plain HTTP is fine).
:::

## Add-ons

The one-command install already includes the
[Kanban board](/features/kanban) and the
[menu-bar icon](/features/background-app#the-menu-bar-icon), so there is nothing
to add for a normal setup. For a lean core without them:

::: code-group

```bash [macOS / Linux]
PRECURSOR_EXTRAS=none sh -c "$(curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh)"
```

```powershell [Windows]
$env:PRECURSOR_EXTRAS = 'none'
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

:::

Both installers read the same variables:

| Variable | Default | Effect |
| --- | --- | --- |
| `PRECURSOR_CHANNEL` | `nightly` | `stable` installs the latest tagged release from PyPI |
| `PRECURSOR_EXTRAS` | `kanban,tray` | the extras to install; `none` for the lean core |
| `PRECURSOR_NO_START` | — | `1` installs the command without registering or starting anything |
| `PRECURSOR_WHEEL` | — | install this wheel (a path or URL) instead of a channel's build |
| `PRECURSOR_REPO` | `lrivallain/precursor` | the repository whose nightly builds to install |

Other [plugins](/features/plugins) release on their own cadence and can be added
at any time, the way any Python package is.

[Agents mode](/features/agents-mode) needs no install step either: the Copilot
SDK ships as a normal dependency, and the native runtime it drives is a one-click
button in **Settings → Agents**.

::: info Package & command names
The PyPI distribution is **`precursor-ai`** (the plain `precursor` name was
already taken). It installs a matching **`precursor-ai`** command plus a shorter
**`precursor`** alias. The import package is `precursor`.
:::
