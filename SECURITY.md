# Security Policy

## Threat model

Precursor is a **single-user, local-first** application that ships with **no
authentication**. It is meant to run on your own machine, bound to `127.0.0.1`.
Anyone who can reach the listening port has full access to the app, its data,
and any tokens it holds. Do not expose it to a network without putting an
authenticating reverse proxy in front of it.

Particularly sensitive surfaces:

- **Command-runner MCP tool** — can execute shell/Python/Node. Keep the Docker
  "jail" enabled; disabling it grants full local-disk access.
- **MCP-over-HTTP transport** — off by default, loopback-only when on.
- **Secrets** — `GITHUB_TOKEN` and provider keys live in `.env` / the local DB
  and are never returned by the API.

## Supported versions

Precursor is pre-1.0 and uses CalVer (`YYYY.M.MICRO`). Only the **latest**
release is supported; fixes ship in a new release rather than as backports.

## Reporting a vulnerability

Please **do not** open a public issue for security reports.

Use GitHub's [private vulnerability reporting](https://github.com/lrivallain/precursor/security/advisories/new)
for this repository. Include a description, reproduction steps, and the affected
version (`GET /api/version` or the Settings panel). We aim to acknowledge
reports within a few days.
