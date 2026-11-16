---
title: Attachments
---

# Attachments

Attach files to a message: images go to the model as **vision** input, documents as
**extracted text**. Drop a file on the composer or use the paperclip (also on *Start a new
chat*, where the first attachment creates the chat).

| Type | Sent as |
| --- | --- |
| Images (PNG, JPEG, …) | Vision content |
| PDF, DOCX, PPTX | Extracted text |
| Text & code (`.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.toml`, `.xml`, `.html`, shell, most languages; any `text/*`) | UTF-8 text |

Scanned (image-only) PDFs have no text layer and there is no OCR: the model only learns that
the file has no extractable text.

## Preview

Click an attachment in a message, the composer or the notes editor to preview it in the app.

<Screenshot src="/screenshots/attachment-preview.png" alt="An image attachment open in the in-app viewer with zoom controls" caption="Inspect attachments without leaving your conversation." />

- Images and text: zoom 25–400% (**+ / -**, **0** to reset, **Ctrl/Cmd + wheel**), **Fit** /
  **Reset**. Text previews show up to 200,000 characters.
- PDFs use the browser's viewer. DOCX, PPTX and others show a fallback.
- **Open in new tab** opens or downloads the original. Close with **Esc** or a click outside.

## How much of a document the model sees

Each document contributes up to **200,000 characters** (~50k tokens). Change it in
**Settings → Model → Prompt budgeting → Max characters per attached document**
(`llm_max_attachment_chars`, 1,000–2,000,000). Longer documents are cut and the model is told
(*"showing the first 200,000 of 412,381 characters"*).

Attachments are resent on every later turn, within **Max input tokens per request** (oldest
turns dropped first). Lower it for small-context models.

## Storage

Bytes live on disk, not in the database: content-addressed under `settings.blobs_dir`
(`.precursor/blobs/<aa>/<bb>/<sha256>`). Identical uploads share a blob, extracted text is
cached next to it (`<sha256>.text-v1.txt`), and a startup sweep (`gc_orphan_blobs`) removes
unreferenced blobs. See the [architecture reference](/reference/architecture#database).

## Tool-result retention

**Settings → System → Storage / retention → `tool_result_retention_days`** (default `0` = keep
forever) replaces old [MCP](/features/mcp) tool results with a short placeholder, keeping the
row and its `tool_calls` metadata. It also removes the model's
[thinking](/features/topics#watching-the-model-think) from older replies. See
[Storage & retention](/features/storage) for the other sweeps and the cleanup cockpit.
