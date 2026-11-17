---
title: Collections
---

# Collections

**Collections** split your topics into separate groups (*Client A*, *Personal*…) so the
sidebar shows only the current one. Each can carry its own GitHub repository and default role.

Every topic is in exactly one collection. A protected **General** collection always exists; if
you never create another, nothing changes.

<Screenshot src="/screenshots/collections.png" alt="The Collections tab in Settings, listing General, Client work, Platform and Research with their topic counts and GitHub repository overrides" caption="Settings → Collections — each one carries a colour accent, a description, and an optional GitHub repository; the protected General collection can't be deleted." />

## Switching collections

The switcher at the top of the **Topics** panel (hidden while only General exists) filters the
topic tree, including **Pinned**. Reminders stay visible regardless.

<Screenshot src="/screenshots/collection-switcher.png" alt="The collection switcher open at the top of the Topics sidebar, listing four collections with their topic counts, plus New collection and Manage collections actions" caption="The switcher lists every collection with its topic count, and lets you create or manage them without leaving the sidebar." />

- The collection is part of the URL (`/topics/client-a/csu/capacity-review`), so links and
  reloads land in the right one. Switching clears the open topic.
- The switcher shows a dot when another collection has unread messages, and per-row counts.
  The Topics badge and tab title count every collection.
- Opening a topic from search, ⌘K, a link or the archive switches to its collection.

## What is filtered

Only **browsing**: the sidebar tree and its search, and *parent topic* pickers (a subtree
can't span collections). Everything else spans all collections: the
[live-session](/features/live-sessions) topic picker, an [agent](/features/agents-mode)'s
linked topic, unread totals, ⌘K search, the archive.

## Moving topics

- Sidebar right-click → **Move to collection**.
- **Topic settings → Collection**.
- **`/collection <name>`** in the composer (bare `/collection` lists them and shows the
  current one).
- On creation: the **Collection** field defaults to the one you're viewing. A sub-topic
  inherits its parent's.

**Sub-topics follow their parent**: moving a topic moves its subtree, re-parenting into
another collection pulls the subtree along, and moving a *sub-topic* to another collection
makes it top-level. Moving changes the readable URL, not the
[permalink](/features/topics#addressing-a-topic).

## Per-collection GitHub repository

Set an `owner/name` repo on a collection. Wherever Precursor needs a topic's repo (linked
issues, comments, Kanban, live-session summaries) it uses the first set of:

1. the **topic's** `github_repo`;
2. the **collection's** repo;
3. the **global** repo from [settings](/guide/configuration).

## Per-collection default role

A collection can set a default [Assistant Role](/features/skills-memory#assistant-roles) for
new topics (**Settings → Collections**). Deleting that role reverts the collection to the
built-in default.

## Managing collections

**Settings → Collections** creates, renames, describes, colours and deletes collections; the
switcher's **New collection…** creates one inline. **Deleting never deletes topics**: you pick
where they go. **General** can't be renamed or deleted.

## Over MCP

On Precursor's [MCP server](/features/mcp):

- `list_topics` and `get_topic` return `collection_id`, `collection` and a `path` starting with
  the collection slug.
- `list_topics` and `create_schedule` accept a **`collection`** argument (name or slug,
  case-insensitive).

Collections group **topics** only. They are unrelated to [workspaces](/features/workspaces)
(Git clones and folders the assistant can edit).
