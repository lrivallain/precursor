---
title: OpenAI-compatible endpoint
---

# OpenAI-compatible endpoint

Use the models of Precursor's active provider from any OpenAI-compatible client —
VS Code, Open WebUI, Continue, the `openai` SDK — by pointing the client at
Precursor instead of at a provider.

<Screenshot src="/screenshots/openai-endpoint.png" alt="The OpenAI-compatible endpoint card in Settings → Model, switched on, showing the base URL, the API key and a Copy VS Code model config button" caption="Settings → Model — the endpoint switched on, with its base URL, its API key and a ready-made VS Code config." />

## Turning it on

**Off by default.** Tick **Serve an OpenAI-compatible endpoint** in **Settings →
Model** (applies immediately). The first time, Precursor mints an API key
(`sk-precursor-…`):

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:<port>/api/openai/v1` |
| API key | sent by the client as `Authorization: Bearer <key>` |

The key stays readable in Settings. **Regenerate** replaces it (old key → `401`).
Turning the endpoint off keeps the key.

## What it relays

Requests go to the **active provider** (**Settings → Model**) and its models.
Supported: **GitHub Copilot, OpenAI, Mistral AI, Hugging Face and Ollama**. With
**Azure AI Foundry** or **Mock** active, or an unconfigured provider, it returns
`503` and the card says why.

| Route | What it does |
| --- | --- |
| `GET /api/openai/v1/models` | The active provider's catalogue, in OpenAI's list shape. Each model also carries `context_window`, `max_output_tokens`, `supported_reasoning_efforts` and `vision` when the provider advertises them. |
| `GET /api/openai/v1/models/{id}` | One model from that catalogue. |
| `POST /api/openai/v1/chat/completions` | A chat completion, streamed (SSE) or not. |

It is a **pure relay**: the client's messages and tools go to the provider
as-is and the client runs its own tool loop (no Precursor MCP servers, skills or
memory).

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

The models appear in the chat model picker under **Precursor**. A trimmed entry:

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

Models are listed explicitly because VS Code's discovery ignores unknown ids.
Each declares `toolCalling` (agent mode needs it), the provider's limits, and
reasoning effort levels where supported.

::: tip A client on another machine
The base URL uses Precursor's bind address (`127.0.0.1` by default), which a
client elsewhere (another computer, a dev container, remote VS Code) can't
reach.
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
With **GitHub Copilot** as the provider, requests use your Copilot account.
Heavy or automated traffic can trip GitHub's abuse detection and suspend your
Copilot access: keep it interactive. VS Code already has Copilot natively; the
endpoint is most useful for other providers and for clients that can't sign in
to Copilot.
:::

## Security

The key is required on every request, but Precursor's own API has no
authentication, so anyone who can reach Precursor can read the key from Settings
(see [Security & deployment model](/reference/architecture#security-deployment-model)).
Keep the default `127.0.0.1` bind unless you add an authenticating proxy.
