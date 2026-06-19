"""Shared validation for image uploads."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB
ALLOWED_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


async def read_validated_image(file: UploadFile) -> tuple[str, bytes]:
    """Validate an uploaded image's MIME + size and return ``(mime, data)``."""
    mime = (file.content_type or "").lower().split(";", 1)[0].strip()
    if mime not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Only image uploads are supported (got '{mime or 'unknown'}').",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File too large (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB).",
        )
    return mime, data
