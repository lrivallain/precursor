"""Extract text/image context from user-message attachments for the LLM.

Image attachments are inlined as ``data:`` URLs for vision-capable providers;
PDF/DOCX/PPTX attachments are best-effort text-extracted and folded into the
user turn as plain-text context. Shared by the turn engine's history hydration.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import zipfile
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from precursor.backend.models import Attachment
from precursor.backend.services.blob_store import read_blob

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_CONTEXT_CHARS = 4_000


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
        for page in reader.pages:
            txt = (page.extract_text() or "").strip()
            if txt:
                chunks.append(txt)
            if sum(len(s) for s in chunks) >= MAX_ATTACHMENT_CONTEXT_CHARS:
                break
        parsed = "\n".join(chunks).strip()
        if parsed:
            return parsed
    except Exception:
        # Fall back to a lightweight best-effort extraction for malformed PDFs.
        pass

    snippets: list[str] = []
    for raw in re.findall(rb"\(([^()]*)\)\s*T[Jj]", data):
        decoded = _unescape_pdf_literal(raw.decode("latin-1", errors="ignore")).strip()
        if decoded:
            snippets.append(decoded)
        if sum(len(s) for s in snippets) >= MAX_ATTACHMENT_CONTEXT_CHARS:
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
                if total >= MAX_ATTACHMENT_CONTEXT_CHARS:
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
                    if total >= MAX_ATTACHMENT_CONTEXT_CHARS:
                        return "\n".join(chunks)
    return "\n".join(chunks)


def _extract_non_image_text(att: Attachment) -> str:
    try:
        data = read_blob(att.sha256)
    except FileNotFoundError:
        logger.warning("Attachment blob %s missing; no text extracted", att.sha256)
        return ""
    if att.mime == "application/pdf":
        return _extract_pdf_text(data)
    if att.mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            return _extract_docx_text(data)
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            return ""
    if att.mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        try:
            return _extract_pptx_text(data)
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            return ""
    return ""


def attachments_to_text_context(atts: list[Attachment]) -> str:
    if not atts:
        return ""
    lines = ["Attached documents:"]
    for att in atts:
        label = att.original_filename or f"attachment-{att.id}"
        lines.append(f"- {label} ({att.mime}, {att.size} bytes)")
        text = _extract_non_image_text(att).strip()
        if text:
            trimmed = text[:MAX_ATTACHMENT_CONTEXT_CHARS]
            if len(text) > len(trimmed):
                trimmed = f"{trimmed}…"
            lines.append("  Extracted text:")
            for row in trimmed.splitlines():
                if row.strip():
                    lines.append(f"  {row}")
        else:
            lines.append(
                "  No extractable text available (file may be scanned/image-only; OCR is not enabled)."
            )
    return "\n".join(lines)
