---
title: Configuration
---

# Configuration

Almost everything in Precursor is configured **at runtime in the app** — under
**Settings** — rather than through environment variables. Every setting has a
built-in default, so the app runs fine with **no `.env` file at all**. The
handful of process-level knobs (host, port, database URL, backup scheduling)
live in `.env`; see the [configuration reference](/reference/configuration).

## Connecting a model

Open **Settings → Model** to pick a provider and enter its credentials. Secrets
are stored in the local database and **never echoed back** by the API — only a
`*_present` boolean is returned.

| Provider | What it is | Credential |
| --- | --- | --- |
| **GitHub Copilot** *(default)* | The Copilot model catalogue (Claude, Gemini, GPT, …), OpenAI-compatible at `api.githubcopilot.com` | a `gho_*` token |
| **Azure AI Foundry** | Azure OpenAI / AI Foundry deployments | endpoint + key + deployment |
| **OpenAI-compatible** | OpenAI, Mistral, Hugging Face, Ollama, or any compatible gateway | base URL + key |
| **Mock** | A deterministic streamed reply for offline development | *(none — automatic fallback)* |

::: tip Offline by default
When no credentials resolve, Precursor automatically uses the **mock provider**,
so the chat flow stays usable with zero setup. Configure a real provider whenever
you're ready.
:::

### Which model gets used

Precursor pins **no** model id of its own. Until you choose one in
**Settings → Model**, the chat model is the first entry in the catalogue the
active provider advertises — so a fresh install works without picking anything,
whatever that provider happens to offer today.

Your choice is honoured for as long as the provider still lists it. Providers
retire models on their own schedule, though, and a retired id would otherwise
fail *every* turn — so if the one you picked disappears, Precursor logs a warning
and falls back to a model that exists rather than erroring. If the catalogue
can't be reached at all, your stored choice is used unchanged: an unreachable
network is no reason to second-guess you.

The catalogue is cached briefly, and re-read immediately when you change provider
or credentials.

### Optional model alternatives

**Settings -> Model -> Model alternatives** lets you organise models into
three categories: **Efficiency** (fast, economical tasks), **Balanced** (everyday
work), and **Intelligence** (complex reasoning). Nothing is configured by default.

<Screenshot src="/screenshots/model-alternatives.png" alt="Compact model alternative summary with configuration counts and an overall catalogue check" caption="Settings keeps just configuration presence and the overall check. Manage presets opens the focused editor; Review configuration goes directly to a problem." />

Settings shows a compact summary: **Chat & live** and **Agents & workflows**
each have a preset/category count, or **Not configured** when empty. Below them
is the overall **Check models** result. **Review configuration** appears when a
preset or provider setup needs attention and opens the affected category directly.

**Manage presets** opens a dedicated modal with all six configurations visible
at once: Efficiency/Balanced/Intelligence are rows, and Chat & live / Agents &
workflows are columns. Each cell shows its preset count and catalogue health.
Click a cell to edit only that list below the overview. Model, effort and context
are compact columns in wide panes and stacked fields on narrow screens. Both
runtime columns remain visible in the overview.

<Screenshot src="/screenshots/model-alternatives-editor.png" alt="Dedicated preset editor with a category by runtime overview and a single compact preset list" caption="See coverage across both runtimes, then edit one short list. Apply to settings transfers your changes to the Settings draft; Cancel discards modal edits." />

They are independent scopes: configure both to cover both chat and agent tasks,
or just the surface you use. Presets remain independent even when both use the
same Copilot subscription. **Apply to settings** updates the Settings draft
without saving it; **Save** in Settings persists both configurations together.
**Cancel**, the modal's close button and Escape discard only the modal's edits.
Topics/chats, live analysis and summaries use the provider scope;
agents and workflow steps use the SDK scope, even when both use the same
GitHub Copilot subscription. Each has its own categories and context controls.
Alternatives never switch provider or credentials.

#### Selecting a preset from a prompt

The prompt's model dropdown includes a **Presets** group, with **Efficiency**,
**Balanced** and **Intelligence** subcategories for the presets you configured.
Empty categories are omitted. Chat/topic composers use the selected provider's
presets; agent composers use the independent Copilot SDK presets.

Select a preset to apply its **model, effort and context together** in one
settings update. Chat presets set the input-token budget; SDK presets set the
context tier. The matching complete profile is checked in the menu. Changing
effort/context separately clears that profile's check when it no longer matches.
Catalogue model choices remain available and keep their existing switching rules.

Search by category, model name/id, or **Presets** to see just your profiles.
Retired ids remain selectable and show **Not listed** when a catalogue is known:
their category can still recover using its listed alternatives. Selecting a
profile never modifies its definition or crosses providers/runtimes.

<Screenshot src="/screenshots/model-presets.png" alt="Prompt model dropdown with a Presets group and Efficiency, Balanced and Intelligence subcategories" caption="Choose a configured model, effort and context as one profile directly from the prompt." />

#### Editing preset definitions

**Add current selection** captures the model, effort and context currently
selected in the composer; **Add preset** adds an alternative. Each model uses
the same searchable dropdown as the prompt composer: filter by name or id,
or by publisher for chat models. Saved ids missing from the current catalogue
remain selectable under **Saved model ids**. To enter a deployment or retired
id manually, type it in the dropdown search and choose **Use model id** (or
press Enter when no catalogue entry matches).
Set each preset's effort and context, move it up or down to control priority,
then **Apply to settings** and **Save** in Settings. Remove every preset to
restore existing behaviour. The shared dropdowns stay above the editor, and
Escape closes an open dropdown before it closes the modal.

Each category/runtime cell in the modal shows a catalogue-check status;
Settings shows an aggregate of the configured runtimes:

| Status | Meaning |
| --- | --- |
| Green **`N/N listed`** | Every preset id is in that provider/runtime's catalogue, with no warnings from the advertised effort/window metadata. |
| Amber **needs review** | A model is no longer listed, a preset is incomplete/duplicated, or its effort/budget needs attention. Each affected preset explains why. |
| Amber **Setup required** | Provider credentials/configuration are missing, the provider resolved to the offline mock, or the Copilot SDK runtime is not enabled/started. |
| Amber **Not checked** | The catalogue failed, returned no models, or cannot be discovered (for example Azure AI Foundry deployment ids). This does **not** mean the model is missing. |
| **Not configured** | No presets in this optional category. |

**Check models** rereads both catalogues without sending inference requests or
saving settings. Checks also run when the settings load, and unsaved edits
update the modal warnings immediately. Settings continues to show the applied
draft until you Apply the modal's changes. Category badges include listed counts when
some presets need review; if no listed alternative remains, the category warns
you explicitly. Switching providers discards the previous catalogue's result.
Unavailable catalogues never turn all saved models into "missing" models.

These are catalogue and advertised-configuration checks, **not test prompts**:
credentials/quota, temporary inference outages and unadvertised effort or
context-tier support can still cause a request to fail. Keep retired ids for
category matching, but add a currently listed alternative before relying on
the category for recovery.

The requested model/configuration is tried first. If it disappears from a known
catalogue, or the provider rejects its model, effort or context configuration,
Precursor tries the category's presets from top to bottom. Each alternative
uses its **own** effort (Auto omits it), plus a chat context-token budget or an
agent context tier. A same-model preset with Auto effort can fix a stale `high`
effort setting without changing models. Chat history is trimmed to a replacement's
budget, capped by its advertised window when available.

Keep the original/retired model in the category so Precursor can identify it.
If a model is assigned to several categories, the selected effort and context
must match one category exactly; otherwise no category is guessed. An
uncategorised selection keeps the existing generic missing-model fallback.
`auto` is managed by the SDK and cannot be categorised.

Saved selections and workflow model pins stay unchanged. A working alternative
is reused for later tool rounds and, for agents, the live session until the
selection/categories change or the session is rebuilt. Later new sessions
try the preferred selection again, so temporary outages do not become permanent
configuration changes. Replies/usage record the model actually used; agent
request recovery appears in the timeline.

Retries are bounded (at most twelve presets per category). They only recover
recognised model/configuration rejections **before any output or agent action**.
Credentials, quota/rate limits, network failures, and errors after output begins
remain visible errors. If every preset is unavailable/rejected, the task fails
clearly rather than moving to a different category. The setting improves
resilience; it cannot guarantee provider availability.

::: tip Use these models from other clients
**Settings → Model** can also serve the active provider's models to VS Code or
any OpenAI-compatible client, behind an API key — see the
[OpenAI-compatible endpoint](/features/openai-endpoint). It's off by default.
:::

::: tip Agents pick their own
[Agent](/features/agents-mode) sessions default to **`auto`**, which lets the
Copilot SDK runtime choose a current model. That's an agents-only value — it is
not a chat model id, and it isn't valid in **Settings → Model**.
:::

## GitHub authentication

Precursor resolves a GitHub token in this order:

1. A token saved in **Settings → GitHub**.
2. Your **GitHub CLI** session (`gh auth token`) if you're signed in via
   `gh auth login`.

So if you already use `gh`, you don't need to set anything. A token needs the
`models:read` fine-grained permission (or Copilot access) for real model
responses. With **no** token at all, Precursor falls back to the mock provider so
the chat flow stays usable offline.

The token value itself is never returned by the API. When a token resolves to a
real GitHub account, the sidebar persona menu also shows your Copilot **AI
credits** and the next reset date.

### Several accounts signed in to `gh`

`gh auth token` follows whichever account is *active*, so with more than one
login the token Precursor gets depends on whoever last ran `gh auth switch` in an
unrelated shell. That's fine interactively, and hopeless for an instance
[started at login](/features/background-app). Pin the login instead:

```bash
PRECURSOR_GITHUB_CLI_USER=your_login
```

Precursor then asks for that account specifically, every time.

### Which repository issues go to

**Settings → GitHub** also holds the default `owner/name` repository used when a
topic creates an issue. It can be overridden at two narrower levels, checked in
this order:

1. the **topic**'s own repository, set in its settings panel;
2. its [collection](/features/collections)'s repository;
3. the global setting above.

## Speech-to-text (Live sessions)

The [live meeting assistant](/features/live-sessions) transcribes audio with
**Azure AI Speech**. Set a Speech **key** and **endpoint** under
**Settings → Speech-to-text**. Until then, live sessions can be created but the
**Record** button stays disabled. Audio streams directly from the browser using a
short-lived token minted by the backend — the subscription key never reaches the
browser, and raw audio is never stored.

## Other settings areas

Precursor's **Settings** panel is organized into tabs, each covered by the
feature it configures:

| Tab | Covers |
| --- | --- |
| **Appearance** | Light/dark/system theme and the reading font, including dyslexia-friendly options like OpenDyslexic, Atkinson Hyperlegible and Lexend — see [Accessibility](/features/accessibility). |
| **Model** | Active provider + credentials, default chat model, prompt budgeting (including how long tool results stay in full; see [context compression](/features/context-compression#automatic-trimming)), and the [OpenAI-compatible endpoint](/features/openai-endpoint) that lets other clients use those models (off by default). |
| **Chat** | Stats sidebar, notifications, and [auto-naming](/features/chats#chats-name-themselves) for new chats. |
| **GitHub** | Token, default repository, issue-context behaviour. |
| **MCP** | Enable [tool servers](/features/mcp), choose which of your own sections the built-in server exposes (off by default), and manage the [Precursor IQ](/features/iq) index: status, rebuild, opt-in embeddings and the Ask answer model. |
| **Collections** | Create and edit [collections](/features/collections). |
| **Agents** | Turn [Agents mode](/features/agents-mode) on/off, set the global [approval policy](/features/agents-mode/running#approval-policy-per-agent), manage [blueprints](/features/agents-mode/orchestration#blueprints-reusable-templates), choose whether new sessions [track file changes for rewind](/features/rewind#restoring-files) (on by default), and bound [timeline retention](/features/storage#why-agent-timelines-have-two-levers). |
| **Workflows** | The [defaults a new pipeline starts from](/features/workflows/building). |
| **Live / Speech-to-text** | Enable the section, pick the fast insights model, set [transcript retention](/features/live-sessions#transcript-retention) and Azure Speech credentials. |
| **Backup** | Periodic copy of the database + attachment blobs into a plain folder. |
| **System** | The scheduled-run timeout, [storage retention](/features/storage) and the [command-runner jail](/features/command-runner). All database-only, and applied without a restart. |

Fleet-wide knobs that aren't per-object — the agent concurrency cap, retry
backoff — are [`.env` settings](#process-level-configuration-env).

## Process-level configuration (`.env`)

For deployment concerns — bind host, port, database URL, log level, shutdown
grace, and backup scheduling — copy `.env.example` to `.env` and uncomment what
you want to override:

```bash
# PRECURSOR_HOST=127.0.0.1
# PRECURSOR_PORT=8000
# PRECURSOR_DATABASE_URL=sqlite+aiosqlite:///./precursor.db
# PRECURSOR_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/precursor
```

See the full list in the [configuration reference](/reference/configuration).

::: warning Postgres needs an extra
The default database is a local SQLite file. To point at PostgreSQL, install the
`postgres` extra (`uv sync --extra postgres`) for the `asyncpg` driver and set
`PRECURSOR_DATABASE_URL` accordingly.
:::
