"""The pairing link as a QR code, rendered on the server (no JavaScript library)."""

from __future__ import annotations

import io

import segno


def qr_svg(url: str) -> str:
    """A standalone SVG of ``url``, black on white so any camera reads it in dark mode too.

    Standalone (with its ``xmlns``, no XML declaration): the dialog shows it as
    an ``<img>`` data URI, which needs the namespace.
    """
    out = io.BytesIO()
    segno.make(url, error="m").save(
        out, kind="svg", xmldecl=False, scale=5, border=2, dark="#000", light="#fff"
    )
    return out.getvalue().decode()
