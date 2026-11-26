"""One-time pairing codes, carried by the QR code shown on the PC.

Kept in memory only: a restart simply invalidates a code nobody used yet.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from precursor.backend.clock import utcnow

PAIRING_TTL = timedelta(minutes=30)
MAX_FAILED_ATTEMPTS = 10


@dataclass
class _Pairing:
    code: str
    expires_at: datetime
    failures: int = 0


class PairingCodes:
    """At most one live code: minting a new one replaces the previous one."""

    def __init__(self) -> None:
        self._current: _Pairing | None = None

    def mint(self) -> tuple[str, datetime]:
        code = secrets.token_urlsafe(16)  # 128 bits
        expires_at = utcnow() + PAIRING_TTL
        self._current = _Pairing(code=code, expires_at=expires_at)
        return code, expires_at

    def pending_until(self) -> datetime | None:
        current = self._current
        if current is None or utcnow() >= current.expires_at:
            return None
        return current.expires_at

    def consume(self, code: str) -> bool:
        """Accept ``code`` once. Too many wrong guesses burn the live code."""
        current = self._current
        if current is None or utcnow() >= current.expires_at:
            self._current = None
            return False
        if secrets.compare_digest(current.code.encode(), code.encode()):
            self._current = None
            return True
        current.failures += 1
        if current.failures >= MAX_FAILED_ATTEMPTS:
            self._current = None
        return False

    def cancel(self) -> None:
        self._current = None


pairing_codes = PairingCodes()
