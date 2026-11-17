"""Conversation canvas schemas (see ``services/canvases.py``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class CanvasWorkspace(BaseModel):
    """The built-in workspace that holds every conversation's canvases."""

    id: int
    slug: str


class CanvasRead(BaseModel):
    name: str
    kind: Literal["html", "drawio", "mermaid", "svg"]
    # Relative to the canvas workspace: ``<conversation_id>/<file>``.
    path: str
    bytes: int
    updated_at: datetime


class CanvasState(BaseModel):
    """JSON a canvas page keeps between visits (``precursor.state`` in the SDK)."""

    state: Any = None
