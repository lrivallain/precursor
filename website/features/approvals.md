# Tool approvals

Decide, per conversation, which tool calls the assistant may make on its own and
which wait for you. Applies to every [topic](/features/topics) and
[chat](/features/chats).

## Three policies

Pick it from the **shield pill** in the composer toolbar, next to the model and
role pickers; it saves immediately. The defaults live in **Settings → Chat**,
one for topics and one for chats.

| Policy | What waits for you |
| --- | --- |
| **Manual** | Every MCP tool call and every Precursor write. |
| **Balanced** | Anything that changes something, or isn't known to be read-only. Reads run freely. |
| **Autonomous** | Nothing. This is the default, so existing conversations behave as before. |

A conversation can also follow the default for its kind (**Default** in the
pill), which is the initial state.

## What is gated

- **MCP tools.** A tool counts as read-only when its server says so through the
  MCP `readOnlyHint` annotation. Silence is treated as "may change something".
- **Precursor's own tools.** Writing workspace files, notes, memories, schedules
  and similar changes are gated; Precursor ships with a hand-checked list of
  which of its built-in tools only read.

## Inline cards

When a call needs your go-ahead the turn **parks**: the assistant stops, and an
amber card appears under the transcript showing the tool, its arguments and the
server.

- **Approve** runs it once.
- **Always allow…** approves it and saves an [allow rule](#rules): choose this
  conversation or every conversation, and optionally limit it to the folder the
  call touched.
- **Deny…** refuses, with optional feedback the model sees.

Once every card is answered the turn **resumes** by itself and the assistant
continues from the results. Sending a new message instead cancels whatever is
still waiting.

## The model can ask first

Under **balanced** and **autonomous**, the model may call a built-in
`request_approval` tool when it judges an action deserves a go-ahead even though
the policy would let it through, for instance something hard to undo. **Manual**
doesn't offer it, since every call already waits. These cards show the model's
reason and read **Go ahead** / **Not yet**; they can't be turned into a standing
allow.

## Pending approvals survive a reload

Pending approvals are stored, not held in the browser. They show on the
conversation as an amber shield badge with a count, distinct from the blue
unread badge, in the sidebar, in the chat list and on side chats. Reload, switch
windows or restart: the cards are still there, and answering in one window
updates the others. Scheduled and unattended turns park the same way and wait
until you open the conversation.

## Rules

Rules are standing answers that sit **in front of the policy**. Each one says
`allow` or `ask` for a server and a tool, optionally only when an argument
matches.

- **Allow** runs the call without a card, even under *manual*.
- **Ask** always parks the call, even under *autonomous*.
- Calls no rule matches follow the policy as before.

Server and tool take `*` and `?` globs (`*` matches anything; Precursor's own
tools live on the server `precursor`). An argument condition names an argument
(dotted for nested ones) and one of:

| Condition | Matches |
| --- | --- |
| **is inside folder** | A path equal to the folder or below it. `..` segments are resolved first, so `notes/../secrets` is not inside `notes`. |
| **equals** | The exact value (text or number). |
| **matches pattern** | A `*` / `?` glob on the value. |

All conditions of a rule must hold, and a rule allows at most five. A call
whose argument is missing or the wrong type never matches.

Evaluation order: the conversation's rules, then global rules; within a level
**ask beats allow**; the first match decides.

Manage them in **Settings → Chat → Approval rules** (global) and in each
topic's or chat's settings (that conversation only). Each rule shows how often
it fired and when it was last used, and can be switched off or deleted. Rules go
with their conversation when it is deleted. *Always allow…* on a card creates
the matching rule for you, prefilled with the call's folder; the condition must
match the call you are approving.

Rules apply to topics and chats. They are never created from the model's own
`request_approval` intents. There is no `deny` effect and no per-workspace scope
yet.

::: tip Agents mode
[Agents mode](/features/agents-mode) has its own approval policy, which governs
the agent's CLI session. The two are independent.
:::
