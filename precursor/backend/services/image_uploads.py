"""Shared validation for chat attachment uploads."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024  # 8 MB
ALLOWED_ATTACHMENT_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _detect_upload_mime(file: UploadFile) -> str:
    declared = (file.content_type or "").lower().split(";", 1)[0].strip()
    if declared:
        return declared
    suffix = Path(file.filename or "").suffix.lower()
    guessed = _MIME_BY_EXTENSION.get(suffix)
    if guessed:
        return guessed
    fallback, _ = mimetypes.guess_type(file.filename or "")
    return (fallback or "").lower()


def _supported_label() -> str:
    return "image/png, image/jpeg, image/webp, image/gif, .pdf, .docx, .pptx"


async def read_validated_attachment(file: UploadFile) -> tuple[str, bytes]:
    """Validate an uploaded attachment MIME + size and return ``(mime, data)``."""
    mime = _detect_upload_mime(file)
    if mime not in ALLOWED_ATTACHMENT_MIMES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported attachment type '{mime or 'unknown'}'. Supported types: {_supported_label()}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB).",
        )
    return mime, data
