"""Installing plugins from inside the app, and restarting to pick them up.

Two things make this deliberately conservative.

**Installing into a live interpreter doesn't work.** Adding a distribution to
``site-packages`` does not register its entry points with the running process,
its routers were never mounted, and its modules aren't imported. Anything short
of a restart leaves a half-installed plugin. So installation runs
out-of-process and the caller is told to restart; :func:`restart_command`
supports doing that from the UI.

**Installing arbitrary packages is remote code execution.** A package's build
and import run with the app's privileges. Precursor binds loopback and is
single-user, but that is a deployment convention, not a guarantee — so the
execution path is off unless the user explicitly turns it on, while merely
*reporting the right command* is always available and carries no risk.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from precursor.backend import uv_receipt

logger = logging.getLogger(__name__)

#: Longest an install may run before we give up on it.
INSTALL_TIMEOUT_SECONDS = 300


@dataclass(slots=True)
class Environment:
    """How this Precursor was installed, and therefore how to extend it."""

    #: "uv-tool" | "uv-venv" | "pip" — which installer owns this environment.
    installer: str
    #: Command to show the user, with a `{package}` placeholder. Deliberately
    #: the portable form: the exact interpreter path is available in `python`,
    #: but pasting it into a UI is noise, and it leaks the filesystem layout
    #: into screenshots and bug reports.
    command_template: str
    #: Interpreter running the app.
    python: str
    #: Whether the server can run the install itself (needs the tool present).
    can_install: bool
    #: Why not, when it can't.
    reason: str | None = None


def _uv() -> str | None:
    return shutil.which("uv")


#: Substituted by the router before the command is shown to a user.
PACKAGE_PLACEHOLDER = "{package}"


def _host_requirement() -> str:
    """The host named exactly as it is installed — extras and wheel URL included.

    ``uv tool install`` rewrites the receipt from its own arguments, so naming a
    bare ``precursor-ai`` here does not mean "leave it as it is": it drops the
    extras (uninstalling the tray) and re-resolves a pinned nightly down to
    whatever the index serves as latest. Restating the requirement keeps adding
    a plugin from being an accidental downgrade.
    """
    installed = uv_receipt.host()
    return installed.as_argument() if installed else uv_receipt.HOST


def _uv_tool_argv(package: str) -> list[str]:
    """Argv adding ``package`` to this tool environment without narrowing it.

    Every sibling is restated, because uv rebuilds the environment from the
    arguments it is given: installing a second plugin with only that plugin
    named would uninstall the first.
    """
    cmd = ["uv", "tool", "install", "--force", _host_requirement()]
    wanted = uv_receipt.canonical_name(package)
    for sibling in uv_receipt.siblings():
        if uv_receipt.canonical_name(sibling.name) != wanted:
            cmd += ["--with", sibling.as_argument()]
    return [*cmd, "--with", package]


def _template(argv: Sequence[str]) -> str:
    """``argv`` as a ``str.format`` template, the package placeholder preserved."""
    parts = []
    for argument in argv:
        if argument == PACKAGE_PLACEHOLDER:
            parts.append(argument)
            continue
        # Braces elsewhere would be read as fields by `.format()`.
        parts.append(shlex.quote(argument).replace("{", "{{").replace("}", "}}"))
    return " ".join(parts)


def detect_environment() -> Environment:
    """Work out which installer owns the environment Precursor runs in.

    ``uv tool install`` creates an isolated environment that ``pip install``
    cannot correctly extend — the tool has to be reinstalled with ``--with`` —
    so telling the two apart is the difference between a command that works and
    one that appears to work and changes nothing.
    """
    prefix = Path(sys.prefix).resolve()
    uv = _uv()
    # `uv tool` environments live under the uv tool directory; that layout is
    # the only reliable signal, since sys.prefix is a normal venv either way.
    # uv resolves its tool directory as UV_TOOL_DIR, then $XDG_DATA_HOME/uv/tools,
    # then the platform default. Missing the middle one would misreport a `uv
    # tool` install as a plain venv — exactly the silent failure this detection
    # exists to prevent.
    tool_dir = os.environ.get("UV_TOOL_DIR")
    xdg = os.environ.get("XDG_DATA_HOME")
    tool_roots = [Path(tool_dir)] if tool_dir else []
    if xdg:
        tool_roots.append(Path(xdg) / "uv" / "tools")
    tool_roots += [
        Path.home() / ".local" / "share" / "uv" / "tools",
        Path.home() / "Library" / "Application Support" / "uv" / "tools",
        Path.home() / "AppData" / "Roaming" / "uv" / "tools",
    ]
    is_uv_tool = any(prefix.is_relative_to(root.resolve()) for root in tool_roots if root.exists())

    if is_uv_tool:
        return Environment(
            installer="uv-tool",
            # A uv tool environment is rebuilt from its requested packages, so a
            # plugin has to be named as part of the tool rather than injected —
            # and so does everything already installed alongside it.
            command_template=_template(_uv_tool_argv(PACKAGE_PLACEHOLDER)),
            python=sys.executable,
            can_install=uv is not None,
            reason=None if uv else "`uv` is not on PATH.",
        )
    if uv is not None:
        return Environment(
            installer="uv-venv",
            command_template="uv pip install {package}",
            python=sys.executable,
            can_install=True,
        )
    return Environment(
        installer="pip",
        command_template="python -m pip install {package}",
        python=sys.executable,
        can_install=True,
    )


def install_command(package: str, env: Environment | None = None) -> list[str]:
    """Argv for installing ``package``. Never shelled out through a shell."""
    env = env or detect_environment()
    if env.installer == "uv-tool":
        return _uv_tool_argv(package)
    if env.installer == "uv-venv":
        return ["uv", "pip", "install", "--python", sys.executable, package]
    return [sys.executable, "-m", "pip", "install", package]


def uninstall_command(package: str, env: Environment | None = None) -> list[str] | None:
    """Argv for removing ``package``, or ``None`` when it can't be expressed."""
    env = env or detect_environment()
    if env.installer == "uv-tool":
        # No per-package removal exists, but the receipt says what the tool was
        # requested with — so "reinstall without this one" is expressible
        # exactly, rather than guessed at.
        dropped = uv_receipt.canonical_name(package)
        siblings = [
            s for s in uv_receipt.siblings() if uv_receipt.canonical_name(s.name) != dropped
        ]
        if len(siblings) == len(uv_receipt.siblings()):
            # Not installed as a sibling: it is a dependency of the host or of
            # one of its extras, and removing it would break the install.
            return None
        cmd = ["uv", "tool", "install", "--force", _host_requirement()]
        for sibling in siblings:
            cmd += ["--with", sibling.as_argument()]
        return cmd
    if env.installer == "uv-venv":
        return ["uv", "pip", "uninstall", "--python", sys.executable, package]
    return [sys.executable, "-m", "pip", "uninstall", "-y", package]


async def run_install(argv: list[str]) -> tuple[int, str]:
    """Run an installer out-of-process. Returns ``(returncode, combined output)``."""
    logger.info("Running plugin installer: %s", " ".join(argv))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=INSTALL_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, f"Installer timed out after {INSTALL_TIMEOUT_SECONDS}s."
    return proc.returncode or 0, out.decode("utf-8", "replace")


def restart_command() -> list[str]:
    """Argv that re-launches this process with the same arguments."""
    return [sys.executable, *sys.orig_argv[1:]] if sys.orig_argv else [sys.executable, *sys.argv]


def restart_in_place() -> None:
    """Replace this process with a fresh one, so discovery re-runs.

    ``execv`` rather than spawn-and-exit: the new process inherits the same pid,
    terminal and listening socket ownership, so a supervisor (or the user's
    shell) sees one continuous service rather than an exit it might not restart.
    """
    argv = restart_command()
    logger.info("Restarting Precursor: %s", " ".join(argv))
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(argv[0], argv)
