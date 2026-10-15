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
- **Always allow here** approves it and allows that tool for the rest of the
  conversation.
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

## Not yet

Per-tool and per-argument **rules** (for instance "always allow reading under
`docs/`") are planned as a later phase; today the choices are the policy, the
per-call answer, and *Always allow here*.

::: tip Agents mode
[Agents mode](/features/agents-mode) has its own approval policy, which governs
the agent's CLI session. The two are independent.
:::
