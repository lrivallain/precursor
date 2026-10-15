# Tool approvals

Decide, per conversation, which tool calls the assistant may make on its own and
which wait for you. Applies to every [topic](/features/topics) and
[chat](/features/chats).

## Policies

Pick one from the **shield pill** in the composer toolbar. The defaults (one for
topics, one for chats) live in **Settings → Chat**.

| Policy | What waits for you |
| --- | --- |
| **Manual** | Every MCP tool call and every Precursor write. |
| **Balanced** | Anything that changes something, or isn't known to be read-only. |
| **Autonomous** | Nothing. This is the default, so existing conversations behave as before. |

An MCP tool counts as read-only when its server says so (the `readOnlyHint`
annotation). Precursor's own tools use a hand-checked list.

## Cards

A call that needs a go-ahead **parks** the turn and shows an inline card:
**Approve** (once), **Always allow…** (saves a rule) or **Deny…** (with optional
feedback for the model). Once every card is answered the turn resumes.

Under *balanced* and *autonomous* the model can also ask first through a
built-in `request_approval` tool.

Pending approvals are stored, so they survive a reload and show as an amber
shield badge in the sidebar.

## Rules

A rule answers `allow` or `ask` for a server and tool (`*` / `?` globs),
optionally only when an argument matches (inside a folder, equals, or a glob).
Rules sit in front of the policy; the conversation's rules come before global
ones, and **ask beats allow**. Manage them in **Settings → Chat** and in each
conversation's settings.

## Unattended runs

When a [scheduled](/features/scheduler) topic run parks, Precursor sends a
browser notification and starts a timeout (60 minutes by default; change it in
Settings or per schedule, `0` never). If nobody answers, the call is denied, the
model is told, and the run finishes on its own.

::: tip Agents mode
[Agents mode](/features/agents-mode) has its own, independent approval policy.
:::
