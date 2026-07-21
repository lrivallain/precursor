"""Background ticker that deletes expired Live transcript segments.

A single lightweight task periodically removes the transcript segments of Live
meeting sessions that ended beyond the retention window (see
``services/live_transcript_retention``), bounding long-term DB growth. Gated by
the same ``scheduler_enabled`` flag as the other tickers; the poll interval
defaults to daily. When retention is disabled the sweep is a cheap no-op, so the
ticker can keep running regardless.
"""

from __future__ import annotations

from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.live_transcript_retention import prune_expired_live_transcripts


class LiveTranscriptTicker(BackgroundPoll):
    task_name = "live-transcript-ticker"
    label = "Live-transcript retention ticker"
    poll_floor = 60

    @property
    def poll_seconds(self) -> int:
        return self._settings.live_transcript_retention_poll_seconds

    async def run_once(self) -> None:
        await prune_expired_live_transcripts()


_ticker: LiveTranscriptTicker | None = None


def get_live_transcript_ticker() -> LiveTranscriptTicker:
    global _ticker
    if _ticker is None:
        _ticker = LiveTranscriptTicker()
    return _ticker
