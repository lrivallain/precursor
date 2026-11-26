"""Thin async wrappers around Microsoft's ``devtunnel`` CLI.

Always ``create_subprocess_exec`` (never a shell), with a timeout: the CLI is
the user's own install and may hang on a network problem.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import shutil

_TIMEOUT = 30.0
_TOKEN_RE = re.compile(r"^Token:\s*(\S+)", re.MULTILINE)


class DevTunnelError(RuntimeError):
    """The CLI failed; the message is safe to show the user."""


def cli_path() -> str | None:
    return shutil.which("devtunnel")


async def run(*args: str, timeout: float = _TIMEOUT) -> tuple[int, str]:
    """Run ``devtunnel <args>`` and return its exit code and combined output."""
    exe = cli_path()
    if exe is None:
        raise DevTunnelError("The devtunnel CLI is not installed.")
    proc = await asyncio.create_subprocess_exec(
        exe,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise DevTunnelError(f"devtunnel {args[0]} timed out.") from None
    return proc.returncode or 0, out.decode(errors="replace")


async def signed_in() -> bool:
    code, out = await run("user", "show")
    return code == 0 and "Logged in as" in out


async def tunnel_exists(tunnel_id: str) -> bool:
    code, _ = await run("show", tunnel_id)
    return code == 0


async def create_tunnel() -> str:
    """Create a private, persistent tunnel and return its id.

    Private: no ``--allow-anonymous``, so the relay only lets the account that
    created it through, after a Microsoft sign-in.
    """
    tunnel_id = f"precursor-{secrets.token_hex(4)}"
    code, out = await run("create", tunnel_id, "--description", "Precursor mobile access")
    if code != 0:
        raise DevTunnelError(f"Could not create the tunnel: {out.strip()[-300:]}")
    return tunnel_id


async def ensure_port(tunnel_id: str, port: int) -> None:
    code, out = await run("port", "create", tunnel_id, "-p", str(port))
    if code != 0 and "already exists" not in out.lower() and "conflict" not in out.lower():
        raise DevTunnelError(f"Could not add port {port} to the tunnel: {out.strip()[-300:]}")


async def connect_token(tunnel_id: str) -> str:
    """A ``connect`` token for this tunnel only (the CLI makes it valid 24 h)."""
    code, out = await run("token", tunnel_id, "--scopes", "connect")
    match = _TOKEN_RE.search(out)
    if code != 0 or match is None:
        raise DevTunnelError("Could not get a connect token for the tunnel.")
    return match.group(1)


async def spawn_host(tunnel_id: str) -> asyncio.subprocess.Process:
    """Start ``devtunnel host <id>``; it runs until stopped."""
    exe = cli_path()
    if exe is None:
        raise DevTunnelError("The devtunnel CLI is not installed.")
    return await asyncio.create_subprocess_exec(
        exe,
        "host",
        tunnel_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
