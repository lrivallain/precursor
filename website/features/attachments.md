---
title: Attachments
---

# Attachments

Attach files to any message and Precursor makes them part of the turn — images
become **vision** content-parts, and documents are **text-extracted**. Bytes are
stored efficiently on disk, not in the database.

## What you can attach

| Type | How it's used |
| --- | --- |
| **Images** (PNG, JPEG, …) | Passed to the model as **vision** content-parts. |
| **PDF** | **Text-extracted** and included as context. |
| **DOCX / PPTX** | **Text-extracted** and included as context. |
| **Text & code files** (`.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.py`, `.ts`, `.sql`, …) | Read as **UTF-8 text** and included as context. |

Drop a file into the composer, or use the attach button. The extracted text (or
image) is included alongside your prompt for that turn.

Text and code files cover the common plain-text and source formats — Markdown,
CSV/TSV, JSON/YAML/TOML, XML/HTML, shell scripts, and most programming languages.
Any `text/*` file is accepted even if its extension isn't in the list.

## How much of a document the model sees

Each attached document contributes up to **200,000 characters** of extracted
text (about 50k tokens), enough for a full research paper. Change this in
**Settings → Model → Prompt budgeting → Max characters per attached document**
(`llm_max_attachment_chars`, from 1,000 to 2,000,000). A longer document is cut,
and the model gets an explicit note, e.g. *"showing the first 200,000 of
412,381 characters"*, so it can tell you what it's missing.

Attachments stay in the conversation: every later turn sends them again, within
the overall **Max input tokens per request** budget, which drops the oldest
turns first. Pick a smaller cap for small-context models.

Scanned (image-only) PDFs have no text layer, and OCR isn't enabled, so the
model only learns that the file has no extractable text.

## How they're stored

Attachment **bytes are not stored in the database**. Each `Attachment` row keeps
only **metadata** plus a `sha256` pointer; the content lives on disk as a
**content-addressed** file under `settings.blobs_dir`
(`.precursor/blobs/<aa>/<bb>/<sha256>`). This keeps the database small and makes
uploads cheap:

- **Deduplication** — identical uploads share the same blob automatically.
- **Garbage collection** — a startup sweep (`gc_orphan_blobs`) reclaims any blob
  no longer referenced by a row.
- **Extracted-text cache** — a document's text is extracted once and saved next
  to its blob (`<sha256>.text-v1.txt`), so later turns don't parse a large PDF
  again. The same sweep removes it together with its blob.

See the [architecture reference](/reference/architecture#database) for details.

## Tool-result retention

Large **tool** results (from [MCP](/features/mcp) calls) can also grow the
database over time. An optional **Settings → System → Storage / retention** knob
(`tool_result_retention_days`, default `0` = keep forever) bounds that growth:
past the configured age, a tool message's content is replaced **in place** with a
short placeholder, while the row and its `tool_calls` metadata are preserved so
conversation history still pairs each tool-call turn with its results. The same
window removes the model's [thinking](/features/topics#watching-the-model-think)
from older replies. The sweep is idempotent and runs best-effort on startup and
periodically.

This is one of several sweeps that bound database growth — see
[Storage & retention](/features/storage) for the rest, and for the cockpit that
runs any of them on demand.
