"""Schemas for the "Refine with AI" text-rewriting utility."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RefineRequest(BaseModel):
    """A block of user-authored text to rewrite.

    ``kind`` is an optional context hint (e.g. ``system_prompt``, ``note``) that
    lets the service tailor its guidance. ``instruction`` is an optional freeform
    steer ("make it shorter", "more formal") appended to the rewrite prompt.
    """

    text: str = Field(min_length=1)
    kind: str | None = None
    instruction: str | None = None


class RefineResponse(BaseModel):
    text: str
    model: str
