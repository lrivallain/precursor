"""The background job that sends the heartbeat and the queued error reports."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from precursor.backend.db import SessionLocal
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.telemetry import appinsights, errors, heartbeat, prefs

logger = logging.getLogger(__name__)


class TelemetryTicker(BackgroundPoll):
    task_name = "telemetry-ticker"
    label = "Telemetry ticker"
    poll_floor = 60

    @property
    def poll_seconds(self) -> int:
        return self._settings.telemetry_poll_seconds

    async def start(self) -> None:
        errors.install_handler()
        await super().start()

    async def stop(self) -> None:
        await super().stop()
        errors.uninstall_handler()

    async def run_once(self) -> None:
        target = prefs.target()
        async with SessionLocal() as session:
            choice = await prefs.load_choice(session)
            allowed = choice.enabled and prefs.blocked_reason() is None and target is not None
            errors.collector.set_enabled(allowed and choice.errors_enabled)
            if not allowed or target is None:
                if choice.decided and not choice.enabled:
                    await prefs.forget_install(session)
                    await session.commit()
                return
            install_id = await prefs.install_id(session)
            envelopes = [
                errors.build(target, report, heartbeat.build_tags(install_id))
                for report in errors.collector.drain()
            ]
            today = datetime.now(UTC).date().isoformat()
            send_heartbeat = await prefs.last_heartbeat(session) != today
            if send_heartbeat:
                envelopes.append(await heartbeat.build(session, target, install_id))
            if await appinsights.post(target, envelopes) and send_heartbeat:
                await prefs.record_heartbeat(session, today)
            await session.commit()


get_telemetry_ticker = TelemetryTicker.instance
