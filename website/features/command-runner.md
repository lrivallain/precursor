---
title: Command runner
---

# Command runner

Let the assistant run **bash / python / node** in a throwaway **Docker jail**
(default) or, if you turn the jail off, on the host. It is the **`cmd-runner`**
[MCP server](/features/mcp): enable it in **Settings → MCP servers**.

<Screenshot src="/screenshots/command-runner.png" alt="Settings → System showing the Command runner (jail) section: a Docker jail toggle, container image, network access toggle and command timeout" caption="Settings → System — the jail is on by default, and network access is off inside it." />

## Settings

Configure it in **Settings → System → Command runner (jail)**. These settings live only
in the database (no `PRECURSOR_CMD_RUNNER_*` variables) and apply to the next
command without a restart.

| Setting | Default | Applies to |
| --- | --- | --- |
| Run commands in a Docker jail | on | — |
| Container image | `python:3.14-slim` | jail (pick one with the interpreters you need, e.g. Node) |
| Allow network access | off (`--network none`) | jail |
| Command timeout (seconds) | 120 | both |
| Max output bytes | 100000 (stdout/stderr truncated past it) | both |
| Memory limit / PID limit / CPU limit | `512m` / 256 / `1` | jail |

## The Docker jail (default)

Each command runs in a fresh container with only its working directory
bind-mounted, then the container is discarded. Enabling `cmd-runner` with the
jail on requires Docker to be running.

## Running on the host (opt-in)

With the jail **off**, commands run directly on the host with the backend's
privileges and full disk access. The settings show a warning while it is off.

::: danger Only disable the jail if you understand the risk
With the jail off, any command the assistant runs has the same access to your
machine that you do. Keep the Docker jail enabled unless you have a specific
reason not to, and never expose Precursor to a network with the jail disabled.
:::

## With workspaces and schedules

- Paired with [workspaces](/features/workspaces), the assistant can edit files
  with the `precursor` workspace tools and then run tests or a build.
- [Scheduled topics](/features/scheduler) can run commands too.
