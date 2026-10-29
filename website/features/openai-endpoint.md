---
title: OpenAI-compatible endpoint
---

# OpenAI-compatible endpoint

Use the models of Precursor's active provider from any OpenAI-compatible client —
VS Code, Open WebUI, Continue, the `openai` SDK — by pointing the client at
Precursor instead of at a provider.

<Screenshot src="/screenshots/openai-endpoint.png" alt="The OpenAI-compatible endpoint card in Settings → Model, switched on, showing the base URL, the API key and a Copy VS Code model config button" caption="Settings → Model — the endpoint switched on, with its base URL, its API key and a ready-made VS Code config." />

## Turning it on

The endpoint is **off by default**. Open **Settings → Model** and tick **Serve
an OpenAI-compatible endpoint**; the switch applies immediately. The first time,
Precursor mints an API key (`sk-precursor-…`), and the card shows what a client
needs:

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:<port>/api/openai/v1` |
| API key | sent by the client as `Authorization: Bearer <key>` |

The key stays **readable** in Settings, on purpose: you'll paste it into more than
one client over time. **Regenerate** replaces it, and clients still holding the
old key are refused (`401`) from their next request. Turning the endpoint off
keeps the key, so switching it back on doesn't break configured clients.

## What it relays

Requests go to the **active provider** — the one picked in **Settings → Model** —
and to the models it lists. The endpoint only serves providers that are simple to
set up and publish a catalogue: **GitHub Copilot, OpenAI, Mistral AI, Hugging
Face and Ollama**. With **Azure AI Foundry** or the **Mock** provider active, or
when the active provider isn't configured (Copilot without a GitHub token, a
missing API key), it answers `503` and the card says why — rather than letting
the offline mock reply with canned text.

| Route | What it does |
| --- | --- |
| `GET /api/openai/v1/models` | The active provider's catalogue, in OpenAI's list shape. Each model also carries `context_window`, `max_output_tokens`, `supported_reasoning_efforts` and `vision` when the provider advertises them. |
| `GET /api/openai/v1/models/{id}` | One model from that catalogue. |
| `POST /api/openai/v1/chat/completions` | A chat completion, streamed (SSE) or not. |

Precursor is a **pure relay** here. The client's messages and function tools go
to the provider as they are, and the client runs its own tool loop — Precursor's
MCP servers, skills and memory aren't involved. Specifically:

- **Messages** — `system`, `developer` (sent as `system`), `user`, `assistant`
  (including its `tool_calls`) and `tool`. Images ride along as `image_url` parts
  of user messages. An image a tool returns — a screenshot in an agent turn — is
  moved into a user message right after the tool results, since providers only
  accept images from the user. Audio and file parts are refused with a `400`
  rather than silently dropped.
- **Options** — `temperature`, `top_p`, `max_tokens` / `max_completion_tokens`,
  `stop`, `seed`, the presence / frequency penalties, `response_format`,
  `tool_choice`, `parallel_tool_calls` and `reasoning_effort`. For models Copilot
  serves only through the Responses API, they're respelled for that API
  (`max_output_tokens`, a flat `tool_choice`, `text.format`); `stop`, `seed` and
  the penalties have no equivalent there and are dropped. Account-side fields such
  as `user`, `store` or `metadata` are ignored.
- **Streaming** — the model's thinking arrives as `reasoning_content` deltas (the
  field VS Code and Open WebUI read), and `stream_options.include_usage` adds the
  closing usage chunk. Tool calls arrive whole, at the end of the turn.
- **Errors** — always in OpenAI's `{"error": {…}}` shape. A provider's own verdict
  (an unknown model, a bad parameter, a `429`) keeps its status. A provider
  refusing *Precursor's* credentials becomes a `502`, so it never reads as the
  client's key being wrong.
- **Usage** — every completion is counted in **Settings → Usage stats** under the
  `openai-endpoint` source.

Not supported: the Responses API (`/v1/responses`, used by Codex CLI), the
Anthropic Messages API (`/v1/messages`, used by Claude Code), embeddings, and
`n` greater than 1.

## Using it from VS Code

VS Code adds a model provider through its **Custom Endpoint** option. The card's
**Copy VS Code model config** button builds the entry for you, from the active
provider's catalogue:

1. Click **Copy VS Code model config**.
2. In VS Code, run **Chat: Manage Language Models** → **Add Models** →
   **Custom Endpoint**, and fill in the prompts with anything.
3. VS Code opens `chatLanguageModels.json`: replace the entry it just added with
   the copied one, and remove the models you don't want in the picker.

The models appear in the chat model picker under **Precursor**. A trimmed entry
looks like this:

```json
[
  {
    "name": "Precursor",
    "vendor": "customendpoint",
    "apiKey": "sk-precursor-…",
    "apiType": "chat-completions",
    "models": [
      {
        "id": "claude-sonnet-5",
        "name": "Claude Sonnet 5",
        "url": "http://127.0.0.1:8000/api/openai/v1/chat/completions",
        "toolCalling": true,
        "vision": true,
        "maxInputTokens": 936000,
        "maxOutputTokens": 64000,
        "thinking": true,
        "supportsReasoningEffort": ["low", "medium", "high"],
        "reasoningEffortFormat": "chat-completions"
      }
    ]
  }
]
```

The models are listed one by one because VS Code's Custom Endpoint discovery only
keeps model ids it already knows — pointed at Precursor's `/models`, it would find
nothing. Every entry declares `toolCalling`, since agent mode hides models without
it; limits come from the provider's catalogue where it publishes them, and
reasoning models get their effort levels, so the picker's **Thinking Effort**
submenu works.

::: tip A client on another machine
The base URL uses the address Precursor listens on, `127.0.0.1` by default. A
client running elsewhere — another computer, a dev container, a remote VS Code
window — can't reach that address.
:::

## Other clients

Anything that takes an OpenAI base URL and key works the same way. With the
`openai` Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/api/openai/v1",
    api_key="sk-precursor-…",
)
reply = client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(reply.choices[0].message.content)
```

## With GitHub Copilot

::: warning Copilot isn't meant to be proxied
With **GitHub Copilot** as the provider, every request reaches Copilot with your
account, just like Precursor's own chats. Copilot isn't intended to be exposed
to other tools this way: heavy or automated traffic through the endpoint can trip
GitHub's abuse detection and suspend your Copilot access. Keep it to
interactive use. In VS Code itself, Copilot's models are already available
natively — the endpoint is most useful for Precursor's other providers, and for
clients that can't sign in to Copilot.
:::

## Security

The endpoint needs the API key on every request, but the key sits in Settings in
clear, and Precursor's own API has no authentication (see
[Security & deployment model](/reference/architecture#security-deployment-model)):
anyone who can reach Precursor can read the key. The endpoint answers on the
address Precursor is bound to — keep the default `127.0.0.1` unless you front it
with your own authenticating proxy.
