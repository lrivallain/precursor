"""Windows process plumbing that POSIX gets for free.

The background app leans on a few process primitives — "is this pid alive",
"ask it to stop", "start it detached", "only one of me" — that POSIX answers
with a signal or a session. Windows answers each differently, and the obvious
translations are the wrong ones for a *windowless* app:

* shelling out to ``tasklist``/``taskkill`` from the tray (a ``pythonw``
  process with no console) flashes a console window on every call, and the tray
  polls every few seconds;
* ``taskkill`` without ``/F`` cannot stop a process that owns no window, so
  "stop" used to wait out its whole timeout and then kill the app hard;
* a ``DETACHED_PROCESS`` child has no console at all, so every ``git``/``gh``
  it runs pops up a console window of its own.

Everything here is importable on any platform; the Windows APIs are only
touched inside the functions, and callers branch on ``os.name`` first.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from functools import lru_cache
from typing import Any, TypedDict

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_TIMEOUT = 0x00000102
_ERROR_ACCESS_DENIED = 5
_ERROR_ALREADY_EXISTS = 183

# Run by a throwaway interpreter, because delivering a console control event
# means *joining* the target's console — which the caller must not do to its
# own. The group id is the target's pid: `detached()` makes it a process-group
# leader, so the event reaches the app and its children but not this helper.
_INTERRUPT_SCRIPT = (
    "import ctypes, sys\n"
    "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
    "pid = int(sys.argv[1])\n"
    "k.FreeConsole()\n"
    "ok = k.AttachConsole(pid) and k.GenerateConsoleCtrlEvent(1, pid)\n"
    "sys.exit(0 if ok else 1)\n"
)


class NoWindowKwargs(TypedDict, total=False):
    creationflags: int


class DetachedKwargs(TypedDict, total=False):
    creationflags: int
    start_new_session: bool


def no_window() -> NoWindowKwargs:
    """``Popen`` kwargs that keep a console program from flashing a window.

    Only matters when the caller has no console of its own (the tray, a login
    item) — a child then gets a brand-new, visible one. Harmless otherwise.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": CREATE_NO_WINDOW}


def detached() -> DetachedKwargs:
    """``Popen`` kwargs for a background child that must outlive its parent.

    On Windows that is a hidden console of its own (``CREATE_NO_WINDOW``) in a
    new process group. The hidden console is inherited by everything the app
    runs, so its ``git``/``gh``/MCP children stay invisible too; the process
    group is what :func:`interrupt` addresses to stop it gracefully.
    """
    if os.name != "nt":
        return {"start_new_session": True}
    return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}


def spawn_detached(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
    """``Popen`` a background child with :func:`detached`, escaping job objects.

    Some terminals (and SSH sessions) run everything inside a Windows job that
    kills its members when the terminal closes, which would take a background
    instance down with the window that happened to start it. Breaking away is
    only allowed when the job permits it, so a refusal falls back to a plain
    spawn rather than failing the start.
    """
    options: dict[str, Any] = {**detached(), **kwargs}
    if os.name != "nt":
        return subprocess.Popen(argv, **options)
    try:  # pragma: no cover - Windows-only path
        return subprocess.Popen(
            argv,
            **{**options, "creationflags": options["creationflags"] | CREATE_BREAKAWAY_FROM_JOB},
        )
    except PermissionError:  # pragma: no cover - Windows-only path
        return subprocess.Popen(argv, **options)


@lru_cache(maxsize=1)
def _kernel32() -> Any:  # pragma: no cover - Windows-only path
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    # Handles are pointer-sized; the ctypes default (a C int) truncates them on
    # 64-bit Windows.
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    return k


def pid_alive(pid: int) -> bool:  # pragma: no cover - Windows-only path
    """Whether ``pid`` is a running process, without spawning anything."""
    if pid <= 0:
        return False
    k = _kernel32()
    handle = k.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Exists, but belongs to someone we may not inspect.
        return bool(ctypes.get_last_error() == _ERROR_ACCESS_DENIED)  # type: ignore[attr-defined]
    try:
        return bool(k.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT)
    finally:
        k.CloseHandle(handle)


def interrupt(pid: int, *, timeout: float = 15.0) -> bool:  # pragma: no cover - Windows-only
    """Send Ctrl+Break to ``pid``'s process group — Windows' closest SIGTERM.

    uvicorn treats ``SIGBREAK`` like ``SIGTERM``: it drains requests, runs the
    lifespan shutdown and exits, which is what lets the database and agent
    sessions close cleanly. Returns whether the event was delivered; the caller
    still has to wait for the process to go, and escalate if it doesn't.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _INTERRUPT_SCRIPT, str(pid)],
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def kill_tree(pid: int, *, force: bool = True) -> None:
    """``taskkill`` ``pid`` and its children, without a console flash."""
    cmd = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        cmd.append("/F")
    subprocess.run(cmd, check=False, capture_output=True, **no_window())


def acquire_single_instance(name: str) -> Any | None:  # pragma: no cover - Windows-only path
    """Hold a named mutex for this process's lifetime; ``None`` if taken.

    The kernel releases it when the process dies, however it dies — which a pid
    file can't promise. Keep the returned handle referenced for as long as the
    claim should hold.
    """
    k = _kernel32()
    handle = k.CreateMutexW(None, False, name)
    if not handle:
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:  # type: ignore[attr-defined]
        k.CloseHandle(handle)
        return None
    return handle


def release(handle: Any) -> None:  # pragma: no cover - Windows-only path
    """Give up a claim made with :func:`acquire_single_instance`."""
    _kernel32().CloseHandle(handle)
