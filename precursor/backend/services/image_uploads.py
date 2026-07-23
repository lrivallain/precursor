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

# ``application/*`` MIME types that are really UTF-8 text (browsers report these
# for structured-text files) and are folded into the turn as plain-text context.
ALLOWED_TEXT_APPLICATION_MIMES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/x-toml",
        "application/sql",
        "application/x-sql",
        "application/javascript",
        "application/x-javascript",
        "application/x-sh",
        "application/x-httpd-php",
        "application/graphql",
        "application/x-ndjson",
        "application/csv",
    }
)

# Text/code file extensions normalized to a canonical MIME. Browsers report wildly
# inconsistent (or empty) MIME types for source files — e.g. ``.ts`` is often
# ``video/mp2t`` and ``.py``/``.go`` are frequently empty — so we key off the
# extension and store a stable ``text/*`` MIME regardless of what the client sent.
_TEXT_MIME_BY_EXTENSION = {
    ".txt": "text/plain",
    ".text": "text/plain",
    ".log": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".mdx": "text/markdown",
    ".rst": "text/x-rst",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".ndjson": "application/x-ndjson",
    ".geojson": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".env": "text/plain",
    ".properties": "text/plain",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".scss": "text/x-scss",
    ".sass": "text/x-sass",
    ".less": "text/x-less",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".jsx": "text/jsx",
    ".ts": "text/typescript",
    ".tsx": "text/tsx",
    ".vue": "text/plain",
    ".svelte": "text/plain",
    ".py": "text/x-python",
    ".pyi": "text/x-python",
    ".rb": "text/x-ruby",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".kts": "text/x-kotlin",
    ".scala": "text/x-scala",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cc": "text/x-c++",
    ".cpp": "text/x-c++",
    ".cxx": "text/x-c++",
    ".hpp": "text/x-c++",
    ".hh": "text/x-c++",
    ".cs": "text/x-csharp",
    ".php": "text/x-php",
    ".swift": "text/x-swift",
    ".sh": "text/x-sh",
    ".bash": "text/x-sh",
    ".zsh": "text/x-sh",
    ".fish": "text/x-sh",
    ".ps1": "text/x-powershell",
    ".bat": "text/plain",
    ".sql": "application/sql",
    ".r": "text/x-r",
    ".pl": "text/x-perl",
    ".lua": "text/x-lua",
    ".dart": "text/x-dart",
    ".tex": "text/x-tex",
    ".graphql": "application/graphql",
    ".gql": "application/graphql",
    ".proto": "text/plain",
    ".dockerfile": "text/plain",
    ".makefile": "text/plain",
    ".mk": "text/plain",
    ".gitignore": "text/plain",
    ".gradle": "text/plain",
}
_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def is_text_attachment_mime(mime: str) -> bool:
    """Return True when ``mime`` denotes UTF-8 text we fold in as plain context."""
    normalized = (mime or "").lower().split(";", 1)[0].strip()
    return normalized.startswith("text/") or normalized in ALLOWED_TEXT_APPLICATION_MIMES


def _detect_upload_mime(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    # Normalize text/code files by extension: browser-declared MIMEs for these are
    # unreliable, so a known text extension always wins.
    text_mime = _TEXT_MIME_BY_EXTENSION.get(suffix)
    if text_mime:
        return text_mime
    declared = (file.content_type or "").lower().split(";", 1)[0].strip()
    if declared:
        return declared
    guessed = _MIME_BY_EXTENSION.get(suffix)
    if guessed:
        return guessed
    fallback, _ = mimetypes.guess_type(file.filename or "")
    return (fallback or "").lower()


def _supported_label() -> str:
    return "image/png, image/jpeg, image/webp, image/gif, .pdf, .docx, .pptx, and text/code files (.txt, .md, .csv, .json, .py, …)"


async def read_validated_attachment(file: UploadFile) -> tuple[str, bytes]:
    """Validate an uploaded attachment MIME + size and return ``(mime, data)``."""
    mime = _detect_upload_mime(file)
    if mime not in ALLOWED_ATTACHMENT_MIMES and not is_text_attachment_mime(mime):
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
