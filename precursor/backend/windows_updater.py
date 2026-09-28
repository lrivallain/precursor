"""Finish a Precursor update on Windows, from outside the environment it replaces.

Windows will not delete or overwrite a file a running process has open, and a
running Precursor holds its whole ``uv tool`` environment open: the interpreter,
every compiled extension it imported, and the ``precursor.exe`` launcher. So
``uv tool install --force`` fails ("os error 32") for as long as the app, the
tray — or the very ``precursor service update`` command asking for it — is still
alive, and can leave the tool half-replaced.

This script is the way out. The caller stops what it can, writes a job file and
launches this with the *base* interpreter — which lives outside the tool
environment — then exits. The script waits for the caller to go, runs the
install, and starts the app and the tray again, logging every step and leaving a
result file that the restarted tray reports.

Deliberately stdlib-only and free of ``precursor`` imports: it runs while that
package is being deleted and rewritten underneath it.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102

# How long the callers get to exit before the install is attempted regardless.
_WAIT_SECONDS = 120.0
# The launchers around the caller (uv's trampoline, the venv's python.exe) exit
# a beat after it does, and antivirus scanners briefly lock freshly written
# files — so a "file in use" failure is retried rather than reported.
_ATTEMPTS = 4
_RETRY_DELAY_SECONDS = 5.0
_LOCK_MARKERS = ("os error 32", "os error 5", "being used by another process", "access is denied")


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT)
    finally:
        kernel32.CloseHandle(handle)


def _no_window() -> dict[str, Any]:
    return {"creationflags": _CREATE_NO_WINDOW} if os.name == "nt" else {}


class _Log:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __call__(self, message: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for line in message.rstrip().splitlines() or [""]:
            self._stream.write(f"{stamp} {line}\n")
        self._stream.flush()


def _wait_for_exit(pids: list[int], log: _Log, *, timeout: float = _WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    for pid in pids:
        while _pid_alive(pid):
            if time.monotonic() >= deadline:
                # Never kill it: a pid we were handed may already belong to
                # something else. The install will say whether it mattered.
                log(f"Process {pid} is still running; installing anyway.")
                break
            time.sleep(0.25)


def _tail(output: str, lines: int = 12) -> str:
    kept = [line for line in output.strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def _install(commands: list[list[str]], log: _Log) -> tuple[bool, int, str]:
    """Run the commands in order until one succeeds.

    Returns ``(ok, index of the command that ran last, its output)``.
    """
    output = ""
    for index, argv in enumerate(commands):
        for attempt in range(1, _ATTEMPTS + 1):
            log(f"$ {subprocess.list2cmdline(argv)}")
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=900,
                    **_no_window(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                output = str(exc)
                log(f"Could not run the installer: {exc}")
                break
            output = f"{result.stdout}\n{result.stderr}".strip()
            if output:
                log(output)
            if result.returncode == 0:
                return True, index, output
            locked = any(marker in output.lower() for marker in _LOCK_MARKERS)
            if not locked or attempt == _ATTEMPTS:
                log(f"Exited {result.returncode}.")
                break
            log(f"Files still in use — retrying in {int(_RETRY_DELAY_SECONDS)}s.")
            time.sleep(_RETRY_DELAY_SECONDS)
    return False, len(commands) - 1, output


def _restart(job: dict[str, Any], log: _Log) -> None:
    cwd = job.get("cwd") or None
    app = job.get("app_command")
    if app:
        log(f"Starting Precursor: {subprocess.list2cmdline(app)}")
        try:
            # Waited on: `service start` returns once the instance answers, so
            # the log says whether it actually came back.
            result = subprocess.run(
                app,
                cwd=cwd,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=240,
                **_no_window(),
            )
            log(f"{result.stdout}\n{result.stderr}".strip() or f"Exited {result.returncode}.")
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"Could not start Precursor: {exc}")
    tray = job.get("tray_command")
    if tray:
        log(f"Starting the tray: {subprocess.list2cmdline(tray)}")
        try:
            flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            subprocess.Popen(
                tray,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **({"creationflags": flags} if os.name == "nt" else {"start_new_session": True}),
            )
        except OSError as exc:
            log(f"Could not start the tray: {exc}")


def run(job: dict[str, Any], stream: TextIO) -> dict[str, Any]:
    log = _Log(stream)
    log(f"Updating Precursor {job.get('from_version') or '?'} → {job.get('to_version') or '?'}")
    _wait_for_exit([int(pid) for pid in job.get("wait_pids", [])], log)
    ok, index, output = _install([list(argv) for argv in job["commands"]], log)
    summaries = list(job.get("summaries") or [])
    if ok:
        message = summaries[index] if index < len(summaries) else "Updated."
    else:
        message = _tail(output) or "The installer failed without saying why."
    result = {
        "ok": ok,
        "message": message,
        "from_version": job.get("from_version"),
        "to_version": job.get("to_version"),
        "finished_at": datetime.now(UTC).isoformat(),
        "log": job.get("log"),
    }
    result_path = job.get("result")
    if result_path:
        Path(result_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    log("Update installed." if ok else "Update failed — restarting the previous build.")
    # Either way: a failed install usually leaves the old build in place, and a
    # stopped app nobody restarts is worse than an old one.
    _restart(job, log)
    return result


def main(argv: list[str]) -> int:
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    log_path = Path(job["log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        try:
            return 0 if run(job, stream)["ok"] else 1
        except Exception as exc:  # pragma: no cover - last-resort diagnostics
            _Log(stream)(f"The updater crashed: {exc!r}")
            raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
