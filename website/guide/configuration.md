---
title: Configuration
---

# Configuration

The settings you usually touch. Nearly everything is set in the app under
**Settings** and stored in the database; every setting has a default, so no
`.env` file is needed. The few process-level variables (host, port, database,
ticker cadences) are listed in the [configuration reference](/reference/configuration).

**Save** applies changes and keeps the panel open. Closing with unsaved changes
asks before discarding them.

## Connecting a model

Open **Settings → Model → Configure provider** and enter credentials. You can
configure several providers; **Apply & refresh models** saves credentials
without changing the **Global default provider** / **Global default model**.

| Provider | Use for | Credential |
| --- | --- | --- |
| **GitHub Copilot** *(default)* | The Copilot catalogue (Claude, Gemini, GPT, …) | a `gho_*` token, or your `gh` session |
| **Azure AI Foundry** | Azure OpenAI / Foundry deployments | endpoint + deployment names + Azure CLI sign-in or key |
| **OpenAI-compatible** | OpenAI, Mistral, Hugging Face, Ollama, any compatible gateway | base URL + key |
| **Mock** | Offline development, canned streamed replies | none |

- With no resolvable credentials, the global default falls back to **Mock**, so
  the app works with zero setup. To disable that, select **Mock** under
  **Configure provider** and untick **Enabled**: unconfigured providers then
  report an error instead.
- Pickers show **✓ Configured** / **○ Not configured** from the saved fields.
  It is not a connection test.
- **Ollama** counts as configured only once a **Base URL** is saved (e.g.
  `http://localhost:11434/v1`); the placeholder is not a value.
- Secrets are stored locally and never returned by the API.

<Screenshot src="/screenshots/provider-configuration.png" alt="Provider configuration picker with green configured and amber not-configured badges" caption="Saved configuration is visible independently of the selected provider." />

### Azure AI Foundry

1. Set the **Endpoint** (classic `https://<resource>.openai.azure.com` or the
   `…/openai/v1` form).
2. Add each deployment under **Deployment names** (type, then **Enter** or
   **Add model**). These are your deployment names, not catalogue model names;
   they aren't verified, and the first is the default.
3. Under **Sign in with**, pick:
   - **Azure CLI (`az login`)** *(default)*: run `az login` on the machine
     running Precursor. Optional **Tenant ID**. Your identity needs an Azure
     OpenAI data-plane role such as *Cognitive Services OpenAI User*.
   - **API key**: paste the resource key. A key saved earlier stays stored
     while you use the CLI; **Remove** deletes it.
4. **Apply & refresh models**, then pick a deployment in the composer.

Chat Completions, Responses and (for `claude` deployments) Claude's Messages
API are selected automatically. API fallback, versions, retries and prompt
caching: [Models reference → Azure AI Foundry](/reference/models#azure-ai-foundry).

<Screenshot src="/screenshots/foundry-models.png" alt="Azure AI Foundry settings with removable model bubbles and an Add model input" caption="Add deployment names individually, then apply the configuration to populate the model picker." />

### Which model gets used

Each chat and topic has one **model** picker in the composer, listing the
models and preset categories of every configured provider. Picking a model saves
its provider, effort and context **for that conversation**; **Use global
default** clears the override. Type an id that isn't in the catalogue to add it
manually. The start screen and workspace composer edit the global default.

<Screenshot src="/screenshots/conversation-providers.png" alt="A single model picker grouping GitHub Copilot and Azure AI Foundry models" caption="Choose a model once; its provider is saved with it." />

- The override applies to replies, retries, `/compact`, summaries, notes,
  naming and scheduled turns of that conversation.
- An explicitly chosen provider that lacks credentials fails visibly; it never
  switches to Mock.
- With no choice, the global model is the first one the default provider
  lists. A retired model falls back to a listed one with a logged warning.
- [Agents](/features/agents-mode) and workflows always use the Copilot SDK and
  default to `auto` (SDK-chosen model, not valid for chat). Live, IQ and the
  OpenAI-compatible endpoint keep their own global defaults.

Full resolution rules: [Models reference](/reference/models#model-resolution).

::: tip Use these models from other clients
**Settings → Model** can serve the configured models to VS Code or any
OpenAI-compatible client behind an API key. Off by default; see the
[OpenAI-compatible endpoint](/features/openai-endpoint).
:::

### Optional model alternatives

**Settings → Presets** defines **categories** (Efficiency, Balanced,
Intelligence, or your own): ordered lists of models, each entry with its own
provider, effort and context. Nothing is configured by default.

<Screenshot src="/screenshots/presets.png" alt="Presets tab with category cards on the left and a searchable model palette on the right" caption="Drag models from the palette into a category, reorder entries by dragging, or press + to add to the selected category." />

- **New category**, or click a suggested Efficiency / Balanced / Intelligence chip.
- Drag models from the palette (or **+**) into a category; drag to reorder.
  **Order is the failover order.**
- Set effort and context per entry (context tier for **Agents (Copilot SDK)**
  entries). Agents and workflows only use entries tagged for the SDK; mix both
  kinds in one category to cover every surface.
- **Check models** verifies the entries against each catalogue without sending
  prompts.

In any composer, the model dropdown's **Presets** group lets you pick a
**category** instead of a model. The first available entry is used; if it is
rejected before any output, the next one is tried. Failover never leaves the
category or switches credentials, and quota/network errors are not retried.

<Screenshot src="/screenshots/model-presets.png" alt="Prompt model dropdown with selectable Efficiency, Balanced and Intelligence categories" caption="Choose a category. The engine resolves its ordered models and their configurations at execution time." />

Limits, status badges and the exact failover rules:
[Models reference → Presets](/reference/models#presets-model-alternatives).

## GitHub authentication

The GitHub token is resolved in this order:

1. a token saved in **Settings → GitHub**;
2. your GitHub CLI session (`gh auth token`).

If you use `gh`, there is nothing to set. Real model replies need `models:read`
(or Copilot access); with no token, the app falls back to Mock. When a token
resolves, the persona menu shows your Copilot **AI credits** and reset date.

Behind an OAuth proxy (e.g. oauth2-proxy), the `X-Forwarded-User` header is
shown in the persona menu ("Signed in via proxy"). It is display only and
grants nothing; the proxy stays in charge of authentication.

### Several accounts signed in to `gh`

`gh auth token` returns the *active* account, which can change under an
instance [started at login](/features/background-app). Pin the login in `.env`:

```bash
PRECURSOR_GITHUB_CLI_USER=your_login
```

### Which repository issues go to

When a topic creates an issue, the repository is the first of: the topic's own
repository, its [collection](/features/collections)'s, then the default
`owner/name` in **Settings → GitHub**.

## Speech-to-text (Live sessions)

[Live sessions](/features/live-sessions) transcribe with **Azure AI Speech**.
In **Settings → Speech-to-text**, set the resource **endpoint** and a sign-in
method; until then **Record** stays disabled.

- **Subscription key**: paste the key.
- **Azure CLI (`az login`)**: no key. Needs the **custom-domain** endpoint
  (`https://<name>.cognitiveservices.azure.com`, not a regional one) and the
  **Cognitive Services Speech User** role. Optional **Tenant ID** (ID or
  domain). If Azure Resource Graph can't find the resource from its subdomain,
  paste its **Resource ID** (`/subscriptions/…/accounts/<name>`).

**Test connection** checks the form before saving. The browser gets a
short-lived token from the backend; no key or CLI credential reaches it, and
audio is never stored.

## Other settings areas

| Tab | Covers |
| --- | --- |
| **Appearance** | Theme and reading font (dyslexia-friendly options): [Accessibility](/features/accessibility). |
| **Chat** | Stats sidebar, notifications, [completion sound](/features/chats#unread-badges-notifications), [auto-naming](/features/chats#chats-name-themselves), max tool-call rounds, [tool approval](/features/approvals) defaults and rules. |
| **Model** | Providers, default model, prompt budgeting ([context compression](/features/context-compression#automatic-trimming)), the [OpenAI-compatible endpoint](/features/openai-endpoint). |
| **Presets** | [Model alternatives](#optional-model-alternatives). |
| **GitHub** | Token, default repository, issue associations. |
| **Speech-to-text** / **Live** | Azure Speech sign-in; the Live section, insights model, [transcript retention](/features/live-sessions#transcript-retention). |
| **MCP servers** | [Tool servers](/features/mcp) and which sections the built-in server exposes (off by default). |
| **Precursor IQ** | [Index](/features/iq) status and rebuild, opt-in embeddings, Ask model. |
| **Skills** / **Roles** / **Memory** | [Skills, roles & memory](/features/skills-memory). |
| **Collections** | [Collections](/features/collections). |
| **Agents** | [Agents mode](/features/agents-mode) on/off and runtime, global [approval policy](/features/agents-mode/running#approval-policy-per-agent), [blueprints](/features/agents-mode/orchestration#blueprints-reusable-templates), [file-change tracking for rewind](/features/rewind#restoring-files), [timeline retention](/features/storage#why-agent-timelines-have-two-levers). |
| **Workflows** | [Defaults for new pipelines](/features/workflows/building), step-input retention. |
| **Definition files** | [Definition files](/features/definitions) (WIP). |
| **Plugins** | Installed [plugins](/features/plugins); plugins can add their own tabs. |
| **Usage stats** | Issue counts over time and database size per table. |
| **Backup** | Periodic copy of the database, attachments and definitions to a folder. |
| **Updates** | [Update channel, checks and notifications](/features/background-app#being-told-about-a-new-build). |
| **System** | Scheduled-run timeout, [storage retention](/features/storage), [command-runner jail](/features/command-runner). |

## Process-level configuration (`.env`)

Deployment settings live in `.env` (in a source checkout: copy `.env.example`;
installed: the `.env` in `precursor service data-dir`):

```bash
PRECURSOR_HOST=127.0.0.1
PRECURSOR_PORT=8000
PRECURSOR_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/precursor
```

PostgreSQL needs the `postgres` extra (`asyncpg`). All variables, including
the agent concurrency cap and retry backoff: [configuration reference](/reference/configuration).
