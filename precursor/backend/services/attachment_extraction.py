"""Extract text/image context from user-message attachments for the LLM.

Image attachments are inlined as ``data:`` URLs for vision-capable providers;
PDF/DOCX/PPTX attachments are best-effort text-extracted and folded into the
user turn as plain-text context. Shared by the turn engine's history hydration.

History hydration replays every past attachment on every turn, so the full
extracted text is cached on disk beside its blob (see ``_TEXT_CACHE_SUFFIX``)
and only capped to the configured ``llm_max_attachment_chars`` at render time.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import zipfile
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from precursor.backend.models import Attachment
from precursor.backend.services.app_settings import (
    DEFAULT_LLM_MAX_ATTACHMENT_CHARS,
    MAX_ATTACHMENT_CHARS,
)
from precursor.backend.services.blob_store import read_blob, sidecar_path
from precursor.backend.services.image_uploads import is_text_attachment_mime

logger = logging.getLogger(__name__)

# Extraction keeps up to the setting's ceiling so the cache serves any cap.
_EXTRACT_MAX_CHARS = MAX_ATTACHMENT_CHARS
# Bump the version whenever an extractor's output changes, to invalidate caches.
_TEXT_CACHE_SUFFIX = "text-v1.txt"

_PDF_MIME = "application/pdf"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def attachments_to_image_urls(atts: list[Attachment]) -> list[str]:
    """Inline image attachments as ``data:`` URLs for vision-capable providers."""
    urls: list[str] = []
    for a in atts:
        try:
            raw = read_blob(a.sha256)
        except FileNotFoundError:
            logger.warning("Attachment blob %s missing; skipping image", a.sha256)
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        urls.append(f"data:{a.mime};base64,{b64}")
    return urls


def is_image_attachment(att: Attachment) -> bool:
    return att.mime.startswith("image/")


def _unescape_pdf_literal(text: str) -> str:
    # Minimal PDF string unescape for common escaped delimiters/newlines.
    return (
        text.replace("\\(", "(")
        .replace("\\)", ")")
        .replace("\\\\", "\\")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\\t", "\t")
    )


def _extract_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        chunks: list[str] = []
        total = 0
        for page in reader.pages:
            txt = (page.extract_text() or "").strip()
            if txt:
                chunks.append(txt)
                total += len(txt)
            if total >= _EXTRACT_MAX_CHARS:
                break
        parsed = "\n".join(chunks).strip()
        if parsed:
            return parsed
    except Exception:
        # Fall back to a lightweight best-effort extraction for malformed PDFs.
        pass

    snippets: list[str] = []
    total = 0
    for raw in re.findall(rb"\(([^()]*)\)\s*T[Jj]", data):
        decoded = _unescape_pdf_literal(raw.decode("latin-1", errors="ignore")).strip()
        if decoded:
            snippets.append(decoded)
            total += len(decoded)
        if total >= _EXTRACT_MAX_CHARS:
            break
    return "\n".join(snippets)


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = sorted(
            name
            for name in archive.namelist()
            if name.startswith("word/")
            and name.endswith(".xml")
            and "/_rels/" not in name
            and not name.endswith(".rels")
            and not (
                name.endswith("styles.xml")
                or name.endswith("settings.xml")
                or name.endswith("fontTable.xml")
                or name.endswith("numbering.xml")
                or name.endswith("webSettings.xml")
            )
        )
        chunks: list[str] = []
        total = 0
        for part in parts:
            root = ET.fromstring(archive.read(part))
            for node in root.iter():
                if node.tag.endswith("}t") and node.text:
                    txt = node.text.strip()
                    if txt:
                        chunks.append(txt)
                        total += len(txt)
                if total >= _EXTRACT_MAX_CHARS:
                    return "\n".join(chunks)
    return "\n".join(chunks)


def _extract_pptx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        slide_paths = sorted(
            n
            for n in archive.namelist()
            if (
                (n.startswith("ppt/slides/slide") and n.endswith(".xml"))
                or (n.startswith("ppt/notesSlides/notesSlide") and n.endswith(".xml"))
            )
        )
        chunks: list[str] = []
        total = 0
        for path in slide_paths:
            xml = archive.read(path)
            root = ET.fromstring(xml)
            for node in root.iter():
                if node.tag.endswith("}t") and node.text:
                    txt = node.text.strip()
                    if txt:
                        chunks.append(txt)
                        total += len(txt)
                    if total >= _EXTRACT_MAX_CHARS:
                        return "\n".join(chunks)
    return "\n".join(chunks)


def _extract_plain_text(data: bytes) -> str:
    """Decode a text/code attachment as UTF-8 (lenient) for LLM context."""
    text = data.decode("utf-8", errors="replace")
    if len(text) > _EXTRACT_MAX_CHARS:
        return text[:_EXTRACT_MAX_CHARS]
    return text


def _has_text_extractor(mime: str) -> bool:
    return mime in (_PDF_MIME, _DOCX_MIME, _PPTX_MIME) or is_text_attachment_mime(mime)


def _extract_text_from_bytes(mime: str, data: bytes) -> str:
    if mime == _PDF_MIME:
        return _extract_pdf_text(data)
    if mime == _DOCX_MIME:
        try:
            return _extract_docx_text(data)
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            return ""
    if mime == _PPTX_MIME:
        try:
            return _extract_pptx_text(data)
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            return ""
    if is_text_attachment_mime(mime):
        return _extract_plain_text(data)
    return ""


def _write_text_cache(sha256: str, text: str) -> None:
    dest = sidecar_path(sha256, _TEXT_CACHE_SUFFIX)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
    except OSError:
        logger.warning("Could not cache extracted text for %s", sha256, exc_info=True)
    finally:
        tmp.unlink(missing_ok=True)


def _cached_text(sha256: str, mime: str) -> str:
    if not _has_text_extractor(mime):
        return ""
    try:
        return sidecar_path(sha256, _TEXT_CACHE_SUFFIX).read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError):
        logger.warning("Unreadable text cache for %s; re-extracting", sha256, exc_info=True)
    try:
        data = read_blob(sha256)
    except FileNotFoundError:
        logger.warning("Attachment blob %s missing; no text extracted", sha256)
        return ""
    text = _extract_text_from_bytes(mime, data).strip()[:_EXTRACT_MAX_CHARS]
    # Cache empty results too: a scanned PDF would otherwise be re-parsed every turn.
    _write_text_cache(sha256, text)
    return text


def extract_attachment_text(att: Attachment) -> str:
    """Full extracted text of a document attachment, cached on disk by content.

    Blocking (file I/O + parsing): warm the cache off the event loop first with
    :func:`warm_attachment_text_cache` in ``asyncio.to_thread``.
    """
    return _cached_text(att.sha256, att.mime)


def warm_attachment_text_cache(docs: list[tuple[str, str]]) -> None:
    """Extract (and cache) the text of each ``(sha256, mime)`` document.

    Takes plain values rather than ORM rows so it is safe to run in a thread.
    """
    for sha256, mime in docs:
        if not mime.startswith("image/"):
            _cached_text(sha256, mime)


def attachments_to_text_context(
    atts: list[Attachment], max_chars: int = DEFAULT_LLM_MAX_ATTACHMENT_CHARS
) -> str:
    if not atts:
        return ""
    lines = ["Attached documents:"]
    for att in atts:
        label = att.original_filename or f"attachment-{att.id}"
        lines.append(f"- {label} ({att.mime}, {att.size} bytes)")
        text = extract_attachment_text(att)
        if text:
            trimmed = text[:max_chars]
            lines.append("  Extracted text:")
            for row in trimmed.splitlines():
                if row.strip():
                    lines.append(f"  {row}")
            if len(text) > len(trimmed):
                # Say so explicitly: a bare ellipsis let the model assume it had
                # the whole document, or guess wrongly at why it was cut short.
                total = f"{len(text):,}{'+' if len(text) >= _EXTRACT_MAX_CHARS else ''}"
                lines.append(
                    f"  [Extracted text truncated: showing the first {len(trimmed):,} of "
                    f"{total} characters. The rest of this document is not visible.]"
                )
        else:
            lines.append(
                "  No extractable text available (file may be scanned/image-only; OCR is not enabled)."
            )
    return "\n".join(lines)
