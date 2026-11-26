"""The pairing link as a QR code, rendered on the server (no JavaScript library)."""

from __future__ import annotations

import segno


def qr_svg(url: str) -> str:
    """An inline SVG of ``url``; black on white so any camera reads it in dark mode too."""
    return str(segno.make(url, error="m").svg_inline(scale=5, dark="#000", light="#fff", border=2))
