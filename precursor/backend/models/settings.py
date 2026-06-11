"""Key/value store for runtime-editable application settings (theme, model, MCP toggles, ...)."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Stored as JSON-encoded text so any serializable shape fits.
    value: Mapped[str] = mapped_column(Text, nullable=False, default="null")
