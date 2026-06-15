"""Built-in MCP server: local command runner (sandboxed).

Runs as a stdio subprocess (like ``fetch_server`` / ``workspace_fs_server``).
Executes ``bash`` / ``python`` / ``node`` commands either inside a throwaway
Docker container (the default "jail") or, when the jail is disabled, directly on
the host with full local disk access. The execution backend lives in
:mod:`precursor.backend.services.cmd_runner`.

Working directory:
- ``workspace_id`` given → the command runs in that Workspace's working tree
  (``workspaces_dir/<slug>[/<subdir>]``), which is bind-mounted into the jail.
- ``workspace_id`` omitted → the command runs in a persistent scratch directory
  (``data_dir/cmd-runner/scratch``), independent of any workspace.

Tools:
- ``runner_info()`` — report jail mode, Docker availability, limits, languages.
- ``list_workspaces()`` — discover workspace ids to bind a run to.
- ``run_command(command, workspace_id=None, timeout=None)`` — ``bash -lc`` a
  shell command.
- ``run_script(language, code, workspace_id=None, timeout=None)`` — run a
  bash/sh/python/node program supplied as text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import Workspace
from precursor.backend.services import cmd_runner
from precursor.backend.services.app_settings import resolve_cmd_runner_config

mcp = FastMCP("cmd-runner")

# Interpreter argv per language. Programs are fed on stdin (``-``/``-s``/piped)
# so we never write a script file into the working tree.
_LANGS: dict[str, list[str]] = {
    "bash": ["bash", "-s"],
    "sh": ["sh", "-s"],
    "python": ["python", "-"],
    "node": ["node"],
}

_HOST_WARNING = (
    "Jail mode is DISABLED: commands run directly on the host with full local "
    "disk access and the backend's privileges. Use only in a trusted, "
    "single-user environment."
)


async def _resolve_workdir(
    workspace_id: int | None, settings: Any
) -> tuple[Path | None, str | None]:
    """Resolve the working directory for a run, or return ``(None, error)``."""
    if workspace_id is None:
        scratch = Path(settings.cmd_runner_scratch_dir)
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch, None
    async with SessionLocal() as session:
        ws = await session.get(Workspace, workspace_id)
    if ws is None:
        return None, f"Workspace {workspace_id} not found"
    root = Path(settings.workspaces_dir) / ws.slug
    if ws.subdir:
        root = root / ws.subdir.strip("/")
    if not root.exists():
        return None, f"Workspace {workspace_id} is not ready yet"
    return root, None


def _result_dict(result: cmd_runner.CommandResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "jailed": result.jailed,
        "truncated": result.truncated,
    }
    if not result.jailed:
        out["warning"] = _HOST_WARNING
    return out


@mcp.tool()
async def runner_info() -> dict[str, Any]:
    """Report how commands will run: jail mode, Docker status, limits, languages.

    Call this first to confirm the runtime. When jail mode is enabled the
    Docker CLI + daemon must be available; when disabled, commands run on the
    host with full disk access (see ``warning``).
    """
    settings = get_settings()
    async with SessionLocal() as session:
        config = await resolve_cmd_runner_config(session)
    info: dict[str, Any] = {
        "jail": config.jail,
        "mode": "docker" if config.jail else "host",
        "image": config.image if config.jail else None,
        "network": config.network,
        "timeout_seconds": config.timeout_seconds,
        "max_output_bytes": config.max_output_bytes,
        "languages": sorted(_LANGS),
        "scratch_dir": settings.cmd_runner_scratch_dir,
    }
    if config.jail:
        ok, detail = cmd_runner.docker_available()
        info["docker_available"] = ok
        info["docker_detail"] = detail
    else:
        info["warning"] = _HOST_WARNING
    return info


@mcp.tool()
async def list_workspaces() -> dict[str, Any]:
    """List available workspaces (id, slug, name) to bind a run to.

    Pass the returned ``id`` as ``workspace_id`` to run a command inside that
    workspace's working tree. Omit it to use the shared scratch directory.
    """
    async with SessionLocal() as session:
        rows = (await session.execute(select(Workspace))).scalars().all()
    return {
        "workspaces": [
            {
                "id": w.id,
                "slug": w.slug,
                "name": w.name,
                "kind": w.kind,
                "ready": w.cloned_at is not None,
            }
            for w in rows
        ]
    }


@mcp.tool()
async def run_command(
    command: str, workspace_id: int | None = None, timeout: int | None = None
) -> dict[str, Any]:
    """Run a shell command (``bash -lc``) in the sandbox and return its output.

    By default the command runs inside a throwaway Docker container with the
    working directory bind-mounted (no host disk access beyond it, network off
    unless configured). If jail mode is disabled it runs directly on the host
    with FULL local disk access — see ``warning`` in the result.

    Pass ``workspace_id`` to run inside that workspace's working tree; omit it to
    use the shared scratch directory.
    """
    settings = get_settings()
    workdir, err = await _resolve_workdir(workspace_id, settings)
    if err is not None:
        return {"error": err}
    assert workdir is not None
    async with SessionLocal() as session:
        config = await resolve_cmd_runner_config(session)
    result = await cmd_runner.run_in_sandbox(
        inner_argv=["bash", "-lc", command],
        workdir=workdir,
        config=config,
        timeout=timeout,
    )
    return _result_dict(result)


@mcp.tool()
async def run_script(
    language: str,
    code: str,
    workspace_id: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run a program written in ``language`` (bash, sh, python, or node).

    The ``code`` is fed to the interpreter on stdin, so nothing is written to
    the working tree. Same sandboxing rules as ``run_command`` apply: jailed in
    Docker by default, or directly on the host (full disk access) when the jail
    is disabled.

    Pass ``workspace_id`` to run inside that workspace's working tree; omit it to
    use the shared scratch directory.
    """
    settings = get_settings()
    inner = _LANGS.get(language.strip().lower())
    if inner is None:
        return {
            "error": (f"Unsupported language '{language}'. Supported: {', '.join(sorted(_LANGS))}.")
        }
    workdir, err = await _resolve_workdir(workspace_id, settings)
    if err is not None:
        return {"error": err}
    assert workdir is not None
    async with SessionLocal() as session:
        config = await resolve_cmd_runner_config(session)
    result = await cmd_runner.run_in_sandbox(
        inner_argv=inner,
        workdir=workdir,
        config=config,
        timeout=timeout,
        stdin_data=code,
    )
    return _result_dict(result)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
