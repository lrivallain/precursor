---
title: Precursor IQ
---

# Precursor IQ

Find anything in your workspace by meaning, not exact wording, and ask
questions that come back with cited answers. The same engine powers the ⌘K
palette and the `retrieve` / `ask` MCP tools. It follows the WorkIQ pattern
(retrieval returns ranked passages plus a grounding block with `[^n]`
citations), applied to Precursor's own content instead of Microsoft 365.

<Screenshot src="/screenshots/iq-palette.png" alt="The command palette ranking results for the query 'retries latency regression': a topic, several passages from a live session's summary, transcript and insight, and topic briefs and messages, each badged with the field that matched, followed by an 'Ask Precursor' row" caption="⌘K ranked by Precursor IQ. The words can come in any order and in any field, and title hits float to the top." />

## What gets indexed

Precursor IQ keeps a derived index of passages of up to about 1,200 characters,
split on paragraph boundaries with a small overlap:

| Source | Passages from | Gated over MCP by |
| --- | --- | --- |
| Topics | title and description | `topics` |
| Topic briefs | the [topic summary](./topic-summary) | `topics` |
| Topic conversations | user and assistant messages, plus text extracted from attachments (PDF, DOCX, PPTX, text) | `messages` |
| Chats | title, description, messages, attachments | `chats` |
| Agents | title, task prompt, final answer | `agents` |
| Live sessions | notes, summary, transcript (with speaker names), insights | `live` |
| Memory | long-term memory entries | `memory` |

Tool output and system messages aren't indexed. Archived items stay in the
index but are hidden from results, so un-archiving brings them back with no
re-index.

### Staying current

Every write to an indexed row queues it for re-indexing **in the same
transaction** as the write. A background indexer processes the queue every few
seconds (`PRECURSOR_IQ_INDEX_POLL_SECONDS`), and every query first processes a
small batch itself, so results include what you just wrote. An hourly check
(`PRECURSOR_IQ_RECONCILE_POLL_SECONDS`) catches bulk edits and deletes that
bypass the queue. On first start, Precursor queues everything once and builds
the index in the background.

**Settings → MCP servers → Precursor IQ** shows the passage count, the queue
depth and which full-text engine is active, and has a **Rebuild index** button.

## How ranking works

Precursor IQ builds two ranked lists and merges them with *Reciprocal Rank
Fusion*:

- **Full-text**: BM25 over the index, using **SQLite FTS5** (accent-insensitive,
  so `reunion` matches `réunion`) or **Postgres** `tsvector`. Each query word
  also matches as a prefix, and a hit on a title counts more than a hit in the
  body. On a SQLite build without FTS5, it falls back to a plain substring scan.
- **Semantic** *(optional)*: cosine similarity between embedding vectors, so
  paraphrases match too. See [Semantic matching](#semantic-matching-embeddings).

Titles that contain every query word get a boost, and recent items get a small
one. Results then keep only the best passage per item, and at most three items
per topic, chat or session, so one long thread can't fill the list.

## Ask

In ⌘K, type a question and press **Tab** (or pick **Ask Precursor**). The top
passages go to your model, which writes a short answer that cites them as
`[n]`. The numbered sources are listed under the answer; press Enter on one to
open it. If nothing matches, Precursor says so and doesn't call the model.

<Screenshot src="/screenshots/iq-ask.png" alt="The command palette in Ask mode: a short answer about a latency regression with numbered citations, followed by the numbered source list (a topic, a live transcript and summary, a release topic message and a meeting insight)" caption="Ask mode: a cited answer grounded only on your own content, with its numbered sources." />

The answer uses your chat model unless you pick another under **Answer model**.
It's a single model call, logged in usage stats as `/iq-ask`.

## Over MCP

The built-in [`precursor` MCP server](/features/mcp) exposes
two tools, each behind its own toggle under **Settings → MCP servers →
Precursor capabilities**. Both are **off by default**:

| Section | Tool | What it returns |
| --- | --- | --- |
| **IQ retrieve** (`iq`) | `retrieve(query, sources?, limit?)` | `markdown` (numbered excerpts to ground on and cite as `[^n]`) and `hits`. Each hit carries its citation `id`, `section`, `entity_id`, `ref`, topic `path`, in-app `url`, matched `field`, `snippet`, `excerpt` and the `accessor` tool that reads the full item. |
| **IQ ask** (`iq_ask`) | `ask(question, sources?)` | `answer` with `[^n]` citations, the `citations` it used and every `sources` passage it was given. Makes one model call. |

IQ never discloses more than the per-section tools would: **every hit is gated
by its own section** (see the table above). With only `iq` and `topics` on, a
caller sees topic titles, descriptions and briefs, but not topic messages
(`messages`) or chats (`chats`). `sources` narrows the search further, e.g.
`["live", "memory"]`.

::: tip Prefer `retrieve` for questions
`search` stays a literal substring match: fast and predictable when you know
the exact words. `retrieve` is the tool for natural-language lookups.
:::

## Semantic matching (embeddings)

Embeddings are **off by default** because each indexed passage is sent once to
your provider's `/embeddings` endpoint, which uses quota. To turn them on,
tick **Semantic matching** in **Settings → MCP servers → Precursor IQ**.

- **Model**: defaults to `text-embedding-3-small`, requested at 256 dimensions.
  On Azure AI Foundry, enter your embeddings **deployment** name. Models that
  can't shorten their vectors keep their native size.
- **Providers**: GitHub Copilot, the OpenAI-compatible providers and Azure AI
  Foundry. With a provider that has no embeddings endpoint, retrieval stays
  full-text only and the card says so.
- **Storage**: vectors are stored next to their passages and compared in-process,
  with no vector database or extra dependency. Changing the provider or the
  model re-embeds everything in the background. Embedding calls appear in usage
  stats as `/iq-embeddings`.

## Configuration

| Setting | Where | Default |
| --- | --- | --- |
| `PRECURSOR_IQ_ENABLED` | env | `true`. When `false`, ⌘K falls back to substring search and `/api/iq/*` returns `404`. |
| `PRECURSOR_IQ_INDEX_POLL_SECONDS` | env | `20` |
| `PRECURSOR_IQ_RECONCILE_POLL_SECONDS` | env | `3600` |
| Semantic matching / embeddings model / answer model | Settings → MCP servers → Precursor IQ | off / `text-embedding-3-small` / chat model |
| IQ retrieve / IQ ask exposure | Settings → MCP servers → Precursor capabilities | off / off |

The background indexer only runs when `PRECURSOR_SCHEDULER_ENABLED` is on, like
Precursor's other background jobs. Without it, the index is still updated on
each query, but embeddings aren't computed.
