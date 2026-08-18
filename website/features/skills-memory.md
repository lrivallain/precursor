---
title: Skills & memory
---

# Skills & memory

Two complementary ways to give the assistant standing context: **skills** are
reusable prompt presets you invoke on demand, and **memory** is long-term notes
injected into every conversation automatically.

## Skills

A **skill** is a reusable prompt preset invoked as **`/name`** in chat — the SPA
expands it inline. Skills are stored as **`SKILL.md` files** using the GitHub
Copilot CLI's format (YAML frontmatter with `name` / `description`, plus a
markdown body of instructions), so they're **interoperable** across tools.

```markdown
---
name: pr-review
description: Review a pull request diff for correctness and style
---

You are reviewing a pull request. Focus on correctness, edge cases, and
adherence to the project's conventions. Be concise and cite line numbers.
```

### Invoking a skill

A skill at the **start** of a message is the classic invocation: everything after
it is the argument, and the transcript keeps showing the literal command while the
model receives the instructions followed by your text.

```
/pr-review here is the diff …
```

You can also reference a skill **anywhere inside** a message. There, the
`/name` token is substituted **in place** — the instructions land exactly where
you wrote them, so the sentence still reads as one instruction:

```
Take the notes below, /rewrite them, then summarise in three bullets.
```

Only **active** skills expand. An unknown or disabled name is left untouched, and
a reference is recognised only at the start of a line or after whitespace and when
not followed by `/` — so paths (`/usr/bin`), URLs (`https://host/rewrite`) and
prose (`and/or`) are never mistaken for a skill call.

::: tip Skills that call skills
A skill's own body may reference another skill. Expansion is capped at two levels
and a cycle (`/a` → `/b` → `/a`) stops rather than recursing — the repeated name
is left literal.
:::

### Where they live

The skills folder is resolved the way the CLI resolves its home:
`COPILOT_HOME` → `XDG_CONFIG_HOME/copilot` → `~/.copilot`, with a
`PRECURSOR_SKILLS_DIR` override. Files live at
`<copilot_home>/skills/<name>/SKILL.md`.

### Discovery & enablement

The `skills` table is reduced to an **enablement record**: a discovered skill is
**disabled until you opt in**, and if its file is renamed or deleted the
enablement row is dropped. Skills authored by other tools show up in the **Skills**
tab and can be enabled per skill. You can enable/disable, edit, export, and delete
— all operating on the file.

Pre-existing Precursor skills created before this model keep working as **legacy**
entries and gain a **Migrate** button that writes the `SKILL.md` and keeps the row
as an enablement record.

### Standing skills: triggering one from a chat description

A [chat description](/features/chats) can reference a skill too. Because the same
`/name` substitution runs on the backend, a description turns a chat into a
**dedicated, single-purpose surface** — no slash command to type, ever.

Set the description to something like:

```
For every message I send: /rewrite
```

…and tick **Use as system prompt** in the chat's settings. The skill's body is
resolved and re-asserted as a mandatory instruction on **every** user turn, so
each message you paste is rewritten without any command.

It works in the default **context** mode too, where the expanded description
rides along as standing discussion-level context instead of being enforced per
turn.

::: tip Which one do I want?
| You want… | Use |
| --- | --- |
| An instruction you invoke *on demand* | a **skill** (`/pr-review`) |
| One chat that always applies the same instruction | a **chat description** referencing a skill |
| A reusable persona across many conversations | an **assistant role** (`/role <name>`) |
| A fact applied to *every* conversation | **memory** |
:::

## Memory

**Memory** is long-term notes injected into the system prompt of topic chats,
flat chats, **and** agent sessions — so standing preferences and facts follow you
everywhere.

Manage memory three ways:

1. **From Settings** — edit the list directly.
2. **From chat** — slash commands:
   - `/memory-store [kind] <content>` — record a note.
   - `/memory-list` — list notes with their ids (needed for updates).
   - `/memory-update <id> [kind] <content>` — refine an existing note.

   Store/update work on the topic, chat, and agent surfaces (and headless
   scheduled topic runs); `/memory-list` is available on topic and chat.
3. **By the model itself** — via the built-in `precursor`
   [MCP server](/features/mcp) tools `store_memory`, `update_memory` (gated by a
   `memory_write` toggle), and the read-only `list_memories`.

::: tip Skills vs memory
Use a **skill** for an instruction you invoke *on demand* (`/pr-review`). Use
**memory** for a fact or preference you want applied to *every* conversation
automatically ("Always answer in French", "Our default branch is `main`").
:::
