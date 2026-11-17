---
title: Skills, roles & memory
---

# Skills, roles & memory

Give the assistant standing context: **skills** are prompts you invoke on
demand, **roles** are personas a conversation adopts, **memory** is notes
injected into every conversation.

<Screenshot src="/screenshots/skills-memory.png" alt="Settings → Skills listing four slash-command skills, each with a description and an enable toggle; one is switched off" caption="Settings → Skills. Each row is a SKILL.md file on disk; the toggle decides whether it is offered as a slash command." />

::: tip Which one do I want?
| You want… | Use |
| --- | --- |
| An instruction you invoke *on demand* | a **skill** (`/pr-review`) |
| One chat that always applies the same instruction | a **chat description** referencing a skill |
| A reusable persona across many conversations | an **assistant role** (`/role <name>`) |
| A fact applied to *every* conversation | **memory** |
:::

## Skills

A **skill** is a prompt invoked as **`/name`**. Skills are **`SKILL.md`** files
in the GitHub Copilot CLI format (frontmatter `name` / `description`, Markdown
body), so they work across tools.

```markdown
---
name: pr-review
description: Review a pull request diff for correctness and style
---

You are reviewing a pull request. Focus on correctness, edge cases, and
adherence to the project's conventions. Be concise and cite line numbers.
```

### Invoking a skill

At the **start** of a message, the rest of the message is the argument; the
transcript shows the command, the model gets the instructions plus your text.

```
/pr-review here is the diff …
```

**Anywhere inside** a message, `/name` is substituted in place:

```
Take the notes below, /rewrite them, then summarise in three bullets.
```

Only **enabled** skills expand, and only at the start of a line or after
whitespace, so paths (`/usr/bin`), URLs and `and/or` are left alone. A skill
may reference another skill: expansion stops at two levels and on cycles.

### Where they live

`<copilot_home>/skills/<name>/SKILL.md`, where the home is `COPILOT_HOME` →
`XDG_CONFIG_HOME/copilot` → `~/.copilot` (override: `PRECURSOR_SKILLS_DIR`).

A discovered skill is **disabled until you enable it** in **Settings → Skills**.
Enable, edit, export and delete act on the file.

### Standing skills: triggering one from a chat description

A [chat description](/features/chats) that references a skill turns the chat
into a single-purpose tool:

```
For every message I send: /rewrite
```

With **Use as system prompt** ticked in the chat's settings, the skill is
enforced on **every** turn. In the default **context** mode the expanded
description is sent as standing context instead.

## Assistant roles

A **role** is a named persona: a system prompt applied to every turn of
whatever adopts it, until you change it. Manage roles in **Settings → Roles**.

- The built-in **`default`** role has an empty prompt; it can be edited but not
  renamed or deleted.
- Roles are referenced by **name**, so they can't be renamed; create a new one.
- Deleting a role reverts its users to `default` and removes the name from agent
  and workflow definition files (a running workflow's saved version loses only
  that role). If an affected definition file can't be checked or written, the
  deletion reports a conflict and keeps the role.
- With [definition files](/features/definitions#roles-as-files) in files mode,
  each role is a `roles/<name>.role.yaml` file edited from this tab.

### Picking a role where you type

The role picker is in the **composer toolbar** of topics, chats (including one
beside a [workspace](/features/workspaces#discussing-a-workspace)),
[agent](/features/agents-mode) tasks and the *Start a new chat* / *Start an
agent task* screens; [live sessions](/features/live-sessions) have it in the
capture toolbar.

- On a creation screen it sets the new conversation's role.
- On an existing conversation it changes the role **saved on that
  conversation**, not just the next message.
- `/role <name>` does the same from the keyboard (topics, chats, agents); a bare
  `/role` opens the picker.

Roles apply to topics, chats, [workspaces](/features/workspaces),
[agents](/features/agents-mode), [live sessions](/features/live-sessions) and
[workflows](/features/workflows/steps#one-voice-for-the-whole-pipeline). A
[collection](/features/collections) can set a default role for new topics.
A role prompt can reference skills (e.g. "For every reply: `/role-skill`").

## Memory

**Memory** is notes injected into the system prompt of topics, chats and agent
sessions ("Always answer in French", "Our default branch is `main`"). Manage it:

1. **From Settings** — edit the list directly.
2. **From chat** — slash commands:
   - `/memory-store [kind] <content>` — record a note.
   - `/memory-list` — list notes with their ids (needed for updates).
   - `/memory-update <id> [kind] <content>` — refine an existing note.
3. **By the model itself** — via the built-in `precursor`
   [MCP server](/features/mcp) tools `store_memory`, `update_memory` (gated by a
   `memory_write` toggle), and the read-only `list_memories`.
