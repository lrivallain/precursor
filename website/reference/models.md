---
title: Models & presets reference
---

# Models & presets reference

How Precursor resolves a model, how presets fail over, and provider-specific
behaviour. For setup, see [Configuration → Connecting a model](/guide/configuration#connecting-a-model).

## Model resolution

- Precursor pins **no** model id. Until you pick one, the global chat model is
  the first entry of the default provider's catalogue.
- A saved choice is used while the provider still lists it. If it disappears
  (retired), Precursor logs a warning and falls back to a listed model. If the
  catalogue can't be reached, the saved choice is used unchanged.
- The catalogue is cached briefly and re-read when you change provider or
  credentials.
- A conversation override (provider, model or category, effort, context) wins
  over the global default and over feature-specific naming models. Side chats
  copy their topic's override; promoting a chat keeps it; child topics don't
  inherit it.
- An explicitly selected provider with missing credentials fails visibly. Only
  conversations on the global default fall back to Mock.
- Agents and workflows always run on the Copilot SDK. Live, IQ and the
  OpenAI-compatible endpoint use their own global defaults.
- Each provider has one saved configuration (no multiple Azure endpoints).

### Stored keys

| Key | Scope | Description |
| --- | --- | --- |
| `llm_provider` | global | Default provider id. Editing a provider's config does not change it. |
| `llm_providers` | global | Map of saved provider configs. Secrets are never returned by the API (only `*_present` booleans). |
| `llm_providers.azure_foundry.deployment` | global | Deployment names, comma-separated (a single name is accepted). |
| `llm_providers.azure_foundry.auth` | global | `azure_cli` (optional `tenant`) or `key`. Unset: a stored `key` means `key`, otherwise `azure_cli`. |
| `llm_providers.ollama.base_url` | global | Must be saved explicitly (e.g. `http://localhost:11434/v1`); the placeholder does not count. |
| `llm_selection` | per chat/topic | `provider`, `model` or `category`, `reasoning_effort`, `context_tokens` (1,000–5,000,000). Null = global defaults. |

## Presets (model alternatives)

Edited in **Settings → Presets**, stored as `model_fallbacks`
(default `{"categories": []}`). No environment variable.

| Limit / field | Value |
| --- | --- |
| Categories | at most 24; id is a lowercase slug (`[a-z0-9-]`, ≤ 40 chars), label ≤ 40 chars |
| Entries per category | at most 12 |
| `provider` | an LLM provider id, or `agents` (Copilot SDK) |
| `model` | explicit id; `auto` is rejected |
| `reasoning_effort` | `""` (Auto), `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `context_tokens` | chat budget, default `128000`, 1,000–5,000,000 |
| `context_tier` | agents only: `default` or `long_context` |

Each provider/model/effort/context combination must be unique across categories.

`llm_model_category` and `agents_model_category` hold the selected category id
(default `null` = direct model). Selecting a category keeps your direct
model/effort/context defaults stored; choosing a direct model clears the
category. `llm_category_preset` is a read-only preview of the category's first
candidate, not a guarantee.

### Failover rules

- Entries are tried **top to bottom**, each with its own model, effort (Auto
  omits it) and context budget/tier. Chat history is trimmed to the
  replacement's budget, capped by its advertised window.
- Failover only covers model/configuration rejections (missing model, unsupported
  effort or context) **before any output or agent action**. Credentials, quota,
  rate limits, network errors and mid-stream errors stay visible errors.
- Failover never leaves the category, switches credentials, or falls back to the
  last direct model. An emptied category fails clearly.
- A working alternative is reused for later tool rounds (and the agent's live
  session) until the selection changes or the session is rebuilt; new sessions
  try the preferred entry again. Replies and usage record the model actually used.
- A direct selection whose model is in a category uses that category's entries
  as alternatives. If the model is in several categories, its effort and context
  must match one exactly, otherwise none is used. Uncategorised selections keep
  the generic missing-model fallback. Keep retired ids in a category so they can
  still be matched.
- Chat, topic, workspace and scheduled turns, summaries, naming and live calls
  honour the selected category. Explicit per-turn or feature overrides and
  agent/workflow model pins take precedence.

### Catalogue check statuses

**Check models** rereads the catalogues without sending inference requests.

| Status | Meaning |
| --- | --- |
| Green **`N/N listed`** | Every entry is in its catalogue, with no effort/window warnings. |
| Amber **needs review** | A model is no longer listed, an entry is incomplete or duplicated, or its effort/budget needs attention. |
| Amber **Setup required** | Missing credentials, provider resolved to Mock, or the Copilot SDK runtime is not enabled/started. |
| Amber **Not checked** | The catalogue failed, was empty, or can't be listed (e.g. Azure AI Foundry deployments). Not the same as missing. |
| **Not configured** | The category has no entries. |

These checks are not test prompts: quota, outages and unadvertised effort or
context support can still fail a request.

## Azure AI Foundry

- **Deployment names** are not discovered or verified; a wrong name fails when
  used. The first name is the default.
- **Endpoints**: both `https://<resource>.openai.azure.com` and the v1 form
  (`…/openai/v1`) work. Precursor tries Responses first on v1, Chat Completions
  first otherwise, falls back to the other API if the deployment doesn't serve
  it, and remembers what worked per deployment.
- **Claude deployments** (name contains `claude`) use the native Messages API
  first (`https://<resource host>/anthropic/`, same key or Azure CLI sign-in),
  then the two OpenAI-compatible APIs.
- **API version**: unset, Responses calls on a classic endpoint use
  `2025-04-01-preview` and Chat Completions use `2024-10-21`. A version you set
  applies to both.
- **Azure CLI sign-in** needs `az` on the server's `PATH` and an Azure OpenAI
  data-plane role (e.g. *Cognitive Services OpenAI User*). Tokens are cached and
  refreshed.
- **Retries**: the SDK retries transient failures twice, honouring retry-delay
  headers. Precursor does not switch API on `429`, `408`, `409`, `5xx` or auth
  failures (same quota), and never replays streamed output. To stay within a
  small quota, MCP tools are [loaded on demand](/features/mcp#tools-loaded-on-demand).
- **Prompt caching** (Claude via Messages API): tool definitions, system prompt
  and conversation are marked with 5-minute `cache_control: {"type": "ephemeral"}`
  breakpoints. Cache reads don't count toward a Claude deployment's input-token
  quota on Foundry; usage reports include cache read/write tokens. Thinking is
  adaptive and follows the effort picker. See
  [Claude on Foundry](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/claude-models#capabilities-and-advanced-features),
  [cache controls](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
  and [quotas](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/claude-models-quotas-limits).
  Azure OpenAI models follow their own
  [caching rules](https://learn.microsoft.com/azure/foundry/openai/how-to/prompt-caching).
