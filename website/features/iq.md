---
title: Precursor IQ
---

# Precursor IQ

Search your own content by meaning and get cited answers, from the **IQ**
section, the ⌘K palette and the `retrieve` / `ask` MCP tools. On by default
(`PRECURSOR_IQ_ENABLED`).

<Screenshot src="/screenshots/iq-palette.png" alt="The command palette ranking results for the query 'retries latency regression': a topic, several passages from a live session's summary, transcript and insight, and topic briefs and messages, each badged with the field that matched, followed by an 'Ask Precursor' row" caption="⌘K ranked by Precursor IQ. The words can come in any order and in any field, and title hits float to the top." />

## What gets indexed

The index holds passages of up to ~1,200 characters, split on paragraphs:

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

Every write queues its row for re-indexing. A background indexer drains the
queue, and each query drains a small batch first, so results include what you
just wrote. An hourly reconcile catches bulk changes. The first start indexes
everything in the background.

**Settings → Precursor IQ** shows the passage count, queue depth and full-text
engine, has **Rebuild index**, and holds the embeddings and answer-model
settings.

## How ranking works

- **Full-text**: BM25 via **SQLite FTS5** (accent-insensitive) or **Postgres**
  `tsvector`; query words match as prefixes and title hits weigh more. Without
  FTS5 it falls back to a substring scan.
- **Semantic** *(optional)*: embedding similarity, so paraphrases match. See
  [Semantic matching](#semantic-matching-embeddings).

Both lists are merged (Reciprocal Rank Fusion), with boosts for titles holding
every query word and for recent items. Each item shows its best passage, and at
most three items per topic, chat or session.

## The IQ section

Open **IQ** from the section rail, `/iq`, or **⌘⇧K** (**Ctrl+Shift+K**) from
anywhere (selected text becomes the question). Ask in plain words:

1. **Matching content** appears first, within a moment. Each row opens the real
   topic, chat, agent or live session, and items whose title matches your
   question also show as **Go to** shortcuts above the answer.
2. The **answer** follows, written by your model from those same numbered
   passages. Click a citation such as `[3]` to open its source; the ones the
   answer relies on are marked **cited** in the list.

<Screenshot src="/screenshots/iq-section.png" alt="The IQ section: a question about the latency regression, a short answer with numbered citation links, and eight numbered sources (topics, a live session's transcript, summary and insight, release messages and a brief), three of them marked cited" caption="The IQ section: ask once, then open the sources behind the answer." />

Questions and answers aren't saved; asking again replaces the answer. Memory
entries can be sources but have no page to open.

## Ask from ⌘K {#ask-from-k}

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

## Semantic matching (embeddings) {#semantic-matching-embeddings}

**Off by default**, since each passage is sent once to your provider's
`/embeddings` endpoint. Tick **Semantic matching** in **Settings → Precursor
IQ**.

- **Provider**: any provider from **Settings → Model** (Azure AI Foundry reuses
  its endpoint and key; Copilot and OpenAI-compatible providers work). If it
  isn't configured or has no embeddings endpoint, retrieval stays full-text and
  the page says so; there is no fallback to another provider.
- **Model**: defaults to `text-embedding-3-small`, requested at 256 dimensions.
  On Azure AI Foundry, enter your embeddings **deployment** name (add it to the
  provider's deployment names to pick it from the list). Models that can't
  shorten their vectors keep their native size.
- **Test embeddings**: embeds one short text with the provider and model
  currently selected, saved or not, and reports the vector size and latency or
  the provider's error, before the whole index starts spending quota.
- **Storage**: vectors are stored in the database and compared in-process (no
  vector database). Changing provider or model re-embeds in the background.
  Usage stats list the calls as `/iq-embeddings`.

## Configuration

| Setting | Where | Default |
| --- | --- | --- |
| `PRECURSOR_IQ_ENABLED` | env | `true`. When `false`, ⌘K falls back to substring search and `/api/iq/*` returns `404`. |
| `PRECURSOR_IQ_INDEX_POLL_SECONDS` | env | `20` |
| `PRECURSOR_IQ_RECONCILE_POLL_SECONDS` | env | `3600` |
| Semantic matching / embeddings provider and model | Settings → Precursor IQ | off / default provider / `text-embedding-3-small` |
| Answer provider and model | Settings → Precursor IQ | default provider / chat model |
| IQ retrieve / IQ ask exposure | Settings → MCP servers → Precursor capabilities | off / off |

The background indexer only runs when `PRECURSOR_SCHEDULER_ENABLED` is on, like
Precursor's other background jobs. Without it, the index is still updated on
each query, but embeddings aren't computed.
