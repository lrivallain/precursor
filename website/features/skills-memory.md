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
