---
title: Running an agent
---

# Running an agent

What a single run looks like while it happens — what you can watch, what it will
stop to ask you, and how it reaches you when you aren't looking.

## Following a run

Give an agent a **task prompt** and it works autonomously, streaming its steps
into a **timeline** you can watch: tool calls visualised inline, permission
prompts surfaced for actions needing approval, and per-session token accounting.

## Approval policy (per agent)

Every agent action is gated by an **approval policy**. There's a global default
in **Settings → Agents** (`manual` asks before every action, `balanced`
auto-approves read-only tools, `autonomous` approves everything), and each agent
either **inherits** it — moving with the global whenever you change it — or
**overrides** it, letting you run a trusted mission more freely without touching
the fleet-wide default.

The policy is read at the start of each turn, so switching it takes effect next
turn with **no session rebuild**, unlike editing the objective or role.

## Picking which tool servers an agent gets (per agent)

An agent attaches **every** [MCP server](/features/mcp) you have enabled, and
that is rarely what a focused agent needs. A modest install registers a few
hundred tools between them and re-sends their schemas on **every turn**, so the
cost is continuous rather than one-off — and an agent handed a browser, a CRM
and a shell will reach for them, whatever its instructions say.

So the settings drawer has an **MCP servers** row. It lists every server enabled
in **Settings → MCP** with its tool count, so the cost is visible where the
choice is made.

| Selection | The agent gets |
| --- | --- |
| **All** (default) | Every enabled server, including ones you add later. |
| One or more servers | Only those. |
| Nothing selected | No tool servers at all — identical to **Tools: off**. |

This is a real allowlist, not a request: the servers you didn't pick are never
attached to the session, so the agent *cannot* call them and their schemas cost
nothing. Asking a model in the prompt not to use a tool is not equivalent — it
reliably reaches for one anyway.

Note what stays dynamic. The list is **not** a fixed catalogue: it's whatever is
registered and enabled right now, so a server you install tomorrow is offered
tomorrow, and an unscoped agent picks it up on its next turn with nothing to
edit. Names are never validated against the local registry either — a scope that
mentions a server this machine doesn't have keeps it (shown struck through,
**red** if nothing by that name is installed, **amber** if it's installed but
switched off) rather than silently dropping it, so an agent survives the trip
between machines.

Changing the scope **rebuilds the session** on the next run, exactly like the
capability toggles — the server set is wired in at build time.

A shared agent driven by a [workflow step](/features/workflows/steps) keeps that
step's own scope for the duration of the step: the step's choice is snapshotted
onto its run and wins, including its "all servers" default, so narrowing an agent
here never silently disarms a pipeline that depends on it.

## Editing an agent (save vs. run)

The **settings drawer** separates *persisting* changes from *acting* on them.
**Save** only writes your edits — changing the objective or role primes the new
instructions for the next run, but never launches a turn. **Save & run** persists
the same edits and starts the objective now, clearing the previous run's
artifacts first; it's disabled while the agent is active.

Editing is safe **while a run is in flight**: a run snapshots the model, role,
approval policy and capability toggles it started with, so changing the
definition mid-flight primes the *next* run rather than moving the ground under
the current one.

## Unread badges & notifications

Agent sessions track unread activity just like topics and chats. When a
background or scheduled agent replies while you aren't looking, its card is
highlighted with an unread count and — when notifications are enabled and the
window is unfocused — a browser notification fires. Opening the session clears
the badge.

### The "agent needs you" signal

Background agents pause when they hit an action needing approval, and the whole
point of running them in the background is that you're *not* watching. So that
block is surfaced **out of band**, the moment it happens:

- A **browser notification fires regardless of focus** the instant an agent
  transitions to **Needs approval**, deep-linking straight to it.
- The **browser tab title** grows a 🔔 bell and a `(n)` count whenever any agent
  is waiting on you.
- The **⌘K command palette** lists the agents that need attention **first**.

Everything waiting on you is also collected into the
[unified inbox](/features/agents-mode/orchestration#the-unified-inbox) across the
top of the dashboard.
