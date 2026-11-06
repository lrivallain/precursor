"""Helpers for model responses that contain structured data."""

from __future__ import annotations

import json
from typing import Any


def extract_json(text: str) -> Any:
    """Return the first JSON object or array, allowing a fence or preamble."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            return value
    return None
