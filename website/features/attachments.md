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

Drop a file into the composer, or use the attach button. The extracted text (or
image) is included alongside your prompt for that turn.

## How they're stored

Attachment **bytes are not stored in the database**. Each `Attachment` row keeps
only **metadata** plus a `sha256` pointer; the content lives on disk as a
**content-addressed** file under `settings.blobs_dir`
(`.precursor/blobs/<aa>/<bb>/<sha256>`). This keeps the database small and makes
uploads cheap:

- **Deduplication** — identical uploads share the same blob automatically.
- **Garbage collection** — a startup sweep (`gc_orphan_blobs`) reclaims any blob
  no longer referenced by a row.

See `services/blob_store.py` and the
[architecture reference](/reference/architecture#database) for details.

## Tool-result retention

Large **tool** results (from [MCP](/features/mcp) calls) can also grow the
database over time. An optional **Settings → System → Storage / retention** knob
(`tool_result_retention_days`, default `0` = keep forever) bounds that growth:
past the configured age, a tool message's content is replaced **in place** with a
short placeholder, while the row and its `tool_calls` metadata are preserved so
conversation history still pairs each tool-call turn with its results. The sweep
is idempotent and runs best-effort on startup and periodically.
