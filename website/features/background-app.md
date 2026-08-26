---
title: Background app
---

# Background app

Precursor started life as a thing you run in a terminal, and for development
that is still the right shape. But an assistant you consult a dozen times a day
shouldn't need a terminal window kept open, a directory to be `cd`'d into, or a
four-command ritual after every reboot.

The **background app** is the answer: one install, a login item, and a menu-bar
icon that tells you whether it's up — plus a self-update that replaces the
`git pull` you used to run by hand.

## One command to install

```bash
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

That installs the latest build, registers it to start when you log in, and
starts it now. There is no clone, no `npm`, and no build step — the published
wheel already carries the SPA, the in-app docs and every plugin frontend.

::: info What it actually runs
`uv tool install` against the wheel for your channel, then
`precursor service install`. Both are ordinary commands you can run yourself;
the script only saves you looking up the current wheel URL.
:::

Prefer stable, tagged releases over the rolling build from `main`?

```bash
PRECURSOR_CHANNEL=stable sh -c "$(curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh)"
```

## Managing the instance

Everything the tray can do has a command behind it, so the app is never
dependent on a GUI being present:

```bash
precursor service status      # is it running, on which port, since when
precursor service start       # start a detached instance
precursor service stop        # stop it
precursor service restart     # bounce it (keeps the port it was on)
precursor service logs -n 100 # tail the instance log
precursor service install     # run at login (and start now)
precursor service uninstall   # remove the login item
```

`status` exits non-zero when nothing is running, so it drops straight into a
shell prompt or a monitoring check. Add `--json` for a machine-readable form.

### How it knows

The supervisor records the instance it started in `runtime.json` inside the
[data directory](/reference/configuration#database-data-directory) — pid, host,
port, URL, version. Everything else is derived from that file plus a liveness
probe, so there is one source of truth and nothing has to guess a port.

If the process dies (a crash, a hard reboot), the next `status` notices the pid
is gone and clears the record rather than leaving a file that claims a port
something else may now own.

Starting is **idempotent**: a login item, a tray click and a manual
`precursor service start` can all race, and none of them ends up with two
instances fighting over one database.

## The menu-bar icon

```bash
precursor tray
```

A filled dot means running, a hollow ring means stopped. The menu offers
**Open Precursor**, **Start** / **Stop** / **Restart**, and an update entry that
becomes *"Update to … and restart"* once a newer build is published.

The tray needs the `tray` extra (`pystray` + `Pillow`), which the install script
includes by default:

```bash
uv tool install --force "precursor-ai[kanban,tray]"
```

::: tip Keep the tray running too
The tray is a separate, disposable process — quitting it does **not** stop
Precursor. Add it to your desktop's login items if you want the icon back
automatically.
:::

## Updating in place

```bash
precursor service check    # is there something newer?
precursor service update   # install it and restart
```

Precursor is installed in one of two shapes and each updates differently, so the
command detects which one you have rather than asking:

| Install | Detected as | Updated by |
| --- | --- | --- |
| `uv tool install precursor-ai` | `uv-tool` | reinstalling the published wheel |
| A clone you run with `uv run precursor` | `source` | `git pull --ff-only` + a plugin frontend rebuild |

The app also exposes the read-only check at `GET /api/version/check`. Applying an
update is deliberately *not* an API call: it replaces the very process serving
the request, so it belongs to the supervisor.

### Channels

| Channel | Tracks | Chosen when |
| --- | --- | --- |
| `nightly` | a rolling prerelease built from every push to `main` | you're running a dev build |
| `stable` | the latest tagged release | you're running a tagged version |

Pin one explicitly with `PRECURSOR_UPDATE_CHANNEL`.

A nightly build isn't ordered — two branches can share a base version — so the
nightly channel compares the **commit** rather than the version number, and a
nightly host is installed together with the plugin wheels built from the same
commit instead of whatever is on PyPI.

## Where the data lives

This is the part that makes a launcher-started app work at all. A login item
runs with its working directory set to `/`, so the old
[relative defaults](/reference/configuration#database-data-directory) would have
created a fresh, empty database wherever it happened to start.

So the defaults now depend on how Precursor was installed:

| Installed as | Database | Data directory |
| --- | --- | --- |
| A source checkout | `./precursor.db` | `./.precursor` |
| A wheel | `<user data dir>/precursor.db` | `<user data dir>` |

…where *user data dir* is `~/Library/Application Support/Precursor` on macOS,
`$XDG_DATA_HOME/precursor` (or `~/.local/share/precursor`) on Linux, and
`%APPDATA%\Precursor` on Windows. `PRECURSOR_DATABASE_URL` and
`PRECURSOR_DATA_DIR` still override both.

::: tip Development is untouched
Keeping the checkout defaults relative is what preserves the worktree workflow:
every clone keeps its own database beside its code, `precursor --dev` still
auto-bumps to a free port, and none of it can collide with the installed
instance — which supervises only the data directory it was configured with.
:::

## Pinning the GitHub account

If you keep several accounts signed in to the GitHub CLI, `gh auth token`
follows whichever one is *active* — so the token Precursor gets depends on
whoever last ran `gh auth switch` in an unrelated shell. That's fine
interactively and hopeless for something started at login.

Name the login instead:

```bash
PRECURSOR_GITHUB_CLI_USER=your_login
```

Precursor then asks for that account specifically, every time. A token saved in
**Settings → GitHub** still wins over the CLI, as before.

## See also

- [Installation](/guide/installation) — the full set of install options
- [Configuration reference](/reference/configuration) — every variable involved
- [Storage & retention](/features/storage) — what accumulates in the data directory
