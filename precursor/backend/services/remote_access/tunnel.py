"""Host a private dev tunnel to this instance and keep it alive.

The tunnel forwards to ``127.0.0.1:<port>``; Precursor stays bound to loopback.
Its id is kept so the address (and the phone's home-screen icon) survives a
restart. A watchdog probes the public address every minute, the way the phone
reaches it, because ``devtunnel host`` can stay running while cut off from
the relay.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Literal

import httpx

from precursor.backend.clock import utcnow
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.remote_access import devtunnel_cli as cli
from precursor.backend.services.setting_rows import store_setting

logger = logging.getLogger(__name__)

TunnelState = Literal["missing_cli", "signed_out", "stopped", "starting", "active", "error"]
SETTING_KEY = "remote_access_tunnel"
HEALTH_PATH = "/api/remote/health"
_URL_RE = re.compile(r"Connect via browser:\s*(https://\S+)")
_READY = "Ready to accept connections"
_FAILURES_BEFORE_RESTART = 2
_TOKEN_TTL = timedelta(hours=12)


async def _load_saved() -> dict[str, object]:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting

    async with SessionLocal() as db:
        row = await db.get(AppSetting, SETTING_KEY)
    try:
        value = json.loads(row.value) if row is not None else {}
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


async def _save(**changes: object) -> None:
    from precursor.backend.db import SessionLocal

    saved = {**await _load_saved(), **changes}
    async with SessionLocal() as db:
        await store_setting(db, SETTING_KEY, saved)
        await db.commit()


class TunnelManager(BackgroundPoll):
    task_name = "remote-tunnel-watchdog"
    label = "Remote access tunnel"
    poll_floor = 30

    def __init__(self) -> None:
        super().__init__()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._tunnel_id: str | None = None
        self._url: str | None = None
        self._ready = False
        self._error: str | None = None
        self._signed_out = False
        self._failures = 0
        self._token: tuple[str, datetime] | None = None
        self._lock = asyncio.Lock()
        self._restore: asyncio.Task[None] | None = None

    @property
    def poll_seconds(self) -> int:
        return 60

    # -- state -------------------------------------------------------------

    @property
    def url(self) -> str | None:
        return self._url if self._ready else None

    def state(self) -> TunnelState:
        if cli.cli_path() is None:
            return "missing_cli"
        if self._running_proc():
            return "active" if self._ready and self._url else "starting"
        if self._signed_out:
            return "signed_out"
        return "error" if self._error else "stopped"

    @property
    def error(self) -> str | None:
        return self._error

    def _running_proc(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the watchdog, and the tunnel if it was on before a restart."""
        await super().start()
        if (await _load_saved()).get("wanted") is True:
            self._restore = asyncio.create_task(self.open(), name="remote-tunnel-restore")

    async def stop(self) -> None:
        await super().stop()
        await self._kill()

    async def open(self) -> None:
        """Start hosting the tunnel (creating it the first time)."""
        async with self._lock:
            if self._running_proc():
                return
            self._error = None
            self._signed_out = False
            try:
                await self._spawn()
            except cli.DevTunnelError as exc:
                self._error = str(exc)
                logger.warning("Remote tunnel: %s", exc)
                return
        await _save(wanted=True)

    async def close(self) -> None:
        async with self._lock:
            await self._kill()
            self._error = None
        await _save(wanted=False)

    async def _spawn(self) -> None:
        if cli.cli_path() is None:
            raise cli.DevTunnelError("The devtunnel CLI is not installed.")
        if not await cli.signed_in():
            self._signed_out = True
            raise cli.DevTunnelError("Sign in to dev tunnels first: devtunnel user login")
        saved = await _load_saved()
        tunnel_id = saved.get("id")
        if not isinstance(tunnel_id, str) or not await cli.tunnel_exists(tunnel_id):
            tunnel_id = await cli.create_tunnel()
            await _save(id=tunnel_id)
        await cli.ensure_port(tunnel_id, self._settings.port)
        self._tunnel_id = tunnel_id
        self._ready = False
        self._url = None
        self._failures = 0
        self._proc = await cli.spawn_host(tunnel_id)
        self._reader = asyncio.create_task(self._read_output(self._proc), name="remote-tunnel-host")

    async def _read_output(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        port_suffix = f"-{self._settings.port}."
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            match = _URL_RE.search(line)
            if match and port_suffix in match.group(1):
                self._url = match.group(1).rstrip("/")
            if _READY in line:
                self._ready = True
        code = await proc.wait()
        if proc is self._proc:
            self._ready = False
            self._error = f"devtunnel host exited (code {code})."

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        self._ready = False
        self._url = None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None

    # -- watchdog ----------------------------------------------------------

    async def _connect_token(self) -> str:
        assert self._tunnel_id is not None
        now = utcnow()
        if self._token is not None and now < self._token[1]:
            return self._token[0]
        token = await cli.connect_token(self._tunnel_id)
        self._token = (token, now + _TOKEN_TTL)
        return token

    async def probe(self) -> bool:
        """Reach this instance through the tunnel's public address."""
        url = self.url
        if url is None:
            return False
        try:
            token = await self._connect_token()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{url}{HEALTH_PATH}", headers={"X-Tunnel-Authorization": f"tunnel {token}"}
                )
            return r.status_code == 200 and r.json().get("ok") is True
        except (httpx.HTTPError, ValueError, cli.DevTunnelError):
            return False

    async def run_once(self) -> None:
        if (await _load_saved()).get("wanted") is not True:
            return
        if not self._running_proc():
            logger.info("Remote tunnel: host not running, restarting it.")
            await self._restart()
            return
        if not self._ready:
            return  # still starting
        if await self.probe():
            self._failures = 0
            return
        self._failures += 1
        if self._failures >= _FAILURES_BEFORE_RESTART:
            logger.warning(
                "Remote tunnel unreachable %s times in a row, restarting.", self._failures
            )
            await self._restart()

    async def _restart(self) -> None:
        async with self._lock:
            await self._kill()
        await self.open()


get_tunnel = TunnelManager.instance
