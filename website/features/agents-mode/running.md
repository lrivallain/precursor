---
title: Running an agent
---

# Running an agent

What you can watch during a run, what the agent stops to ask, and how it reaches
you when you aren't looking.

## Following a run

Give an agent a **task prompt** and it works on its own, streaming into a
**timeline**: tool calls inline, approval prompts, and per-session token usage.

The timeline reads as **exchanges**: your prompt, the work, the answer. Once a
turn has its answer, its steps fold into one line
(*Worked for 3m 13s · 19 tool calls · 7 updates*); click it to expand. In-flight
turns and turns waiting on an approval stay open. In a
[mission](/features/agents-mode/missions), the goal loop's nudge between steps
shows as a *Continued autonomously* marker. To never fold, switch off **Fold
finished turns** under **Show** in the insights sidebar.

<Screenshot src="/screenshots/agents-activity.png" alt="An agent's Activity tab: each follow-up prompt is followed by a folded Worked-for summary and the answer, which links to the result version it published" caption="Finished turns fold above their answers. Each answer links to the result version it published." />

An agent that publishes a result gets a **Result** tab next to the timeline;
see [Reading an agent's result](/features/agents-mode/artifacts-state#reading-an-agent-s-result).

**Run agent** (play button in the header) starts a new execution of the saved
task. It doesn't send the composer draft, and is disabled while the agent is
pending, running, awaiting approval or interrupted, or when the runtime is
unavailable. Use **Stop** for an active run and **Resume** to continue an
interrupted turn.

### Watching an agent think

As in [chats and topics](/features/topics#watching-the-model-think): a pulsing
**Thinking…** row previews the current step; click it to read the full
thinking as it streams. Afterwards it stays as a collapsed **Thinking** area on
the message it led to (or just before the tool call it led to). Stopping keeps
what arrived. **Thinking** under **Show** hides it all.

<Screenshot src="/screenshots/agents-thinking.png" alt="An agent's answer with its Thinking area opened above it, showing two titled steps of the model's reasoning" caption="A round's thinking, opened on the answer it led to. Collapsed, the row shows only the latest step." />

| Model | Thinking shown |
| --- | --- |
| Claude | Live, only when a reasoning effort is picked (composer or **Settings → Agents**) |
| Gemini | Once the round is done, with its message |
| GPT | None (reasoning stays encrypted in the runtime) |

### Keeping a long run within the window

The runtime compacts automatically when the context window runs low. To compact
now, use **`/compact [focus]`** or **Compact context** under **Usage** in the
insights sidebar. Each compaction leaves a *context compacted* marker with the
tokens freed; **Usage** also breaks down the window. See
[context compression](/features/context-compression#compacting-an-agent).

To drop only the last few turns, [rewind](/features/rewind#in-agent-sessions)
from the timeline rail or a prompt's toolbar; earlier turns stay in context.

## Message links

**Copy link to message** in a prompt's or reply's action bar gives:

```text
/agents/<agent-uuid>#event=<event-link-uuid>
```

It opens **Activity**, unfolds whatever hides the message (including earlier
runs) and highlights it. Links survive reloads and restarts. If a rewind, clear,
retention sweep or deletion removed the event, the view says **This message is
no longer available**. Streaming text and approval cards aren't linkable. Links
grant no access by themselves.

Before each prompt the agent also receives a hidden index of the latest 50
linkable messages of its own execution (excerpts + URLs), so its answers can
link back to a specific earlier reply.

## When a model disappears or rejects its configuration

Under **Settings → Presets**, add [model alternatives](/guide/configuration#optional-model-alternatives)
tagged **Copilot agents** to a category (other providers' entries don't apply).
The **Agent model** dropdown then offers those categories under **Presets**;
explicit model pins on an agent or workflow win over the default category.

If a model is missing or rejects its effort/context, the run switches to the
next preset in the same category, keeping the conversation; the timeline shows
it. It never changes category and never replays a turn once output or tool
activity has started. Credential, quota and network errors fail normally.
Without presets, the SDK's `auto` fallback for retired model ids still applies.

## Approval policy (per agent)

| Policy | Behaviour |
| --- | --- |
| `manual` | Ask before every action |
| `balanced` | Auto-approve read-only tools |
| `autonomous` | Approve everything |

The global default is in **Settings → Agents**. Each agent either **inherits**
it (and follows later changes) or **overrides** it. The policy is read at the
start of each turn, so a change applies next turn without a session rebuild.

## Picking which tool servers an agent gets (per agent)

By default an agent gets every enabled [MCP server](/features/mcp), and their
tool schemas are re-sent on every turn. The settings drawer's **MCP servers**
row (with each server's tool count) narrows that:

| Selection | The agent gets |
| --- | --- |
| **All** (default) | Every enabled server, including ones added later |
| One or more servers | Only those |
| Nothing selected | No tool servers (same as **Tools: off**) |

Unpicked servers are never attached, so the agent can't call them and pays
nothing for them. Names aren't validated against this machine: an unknown name
is kept and shown struck through (**red** = not installed, **amber** =
installed but disabled), so an imported agent keeps its scope.

Changing the scope rebuilds the session on the next run. A
[workflow step](/features/workflows/steps#picking-which-tool-servers-a-step-gets)
driving a shared agent uses the step's own scope for that run.

## Editing an agent (save vs. run)

In the settings drawer, **Save** only stores the edits (a new objective or role
applies to the next run). **Save & run** stores them and starts the objective
now, clearing the previous run's artifacts; it is disabled while the agent is
active. A run in flight keeps the model, role, policy and capabilities it
started with.

## Session lifetime and runtime recovery

- A run at rest (idle, completed, failed or blocked, not waiting on approval)
  for **15 minutes** has its CLI session disconnected to free memory. The next
  message, **Resume** or answer reconnects; only "approve for this session"
  grants are reset.
- If the CLI process dies, the next agent request or the watchdog (checks every
  minute) restarts it. A turn in flight is marked **interrupted**; use
  **Resume**.

## Unread badges & notifications

When a background or scheduled agent replies while you aren't looking, its card
gets an unread count and, if notifications are on and the window is unfocused,
a browser notification fires. Opening the agent clears it.

### The "agent needs you" signal

When an agent moves to **Needs approval**:

- a browser notification fires **regardless of focus**, linking to the agent;
- the tab title shows 🔔 and a `(n)` count while any agent waits;
- the ⌘K palette lists waiting agents first;
- it appears in the [unified inbox](/features/agents-mode/orchestration#the-unified-inbox).
