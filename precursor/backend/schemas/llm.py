from __future__ import annotations

from pydantic import BaseModel


class LLMModelRead(BaseModel):
    id: str
    name: str
    publisher: str = ""
    summary: str = ""
    tags: list[str] = []
    context_window: int | None = None
