---
title: Plugin catalogue
description: Plugins that extend Precursor, and how to get yours listed here.
---

<script setup>
import { data as plugins } from "../.vitepress/catalog.data.mts";
</script>

# Plugin catalogue

Precursor is deliberately small. Anything that isn't "topics, chat, GitHub"
belongs in a [plugin](/features/plugins) — a Python package that can bring a
whole section, API routes, settings and MCP tools, and that you install and
remove independently of the app.

These are the ones we know about. Each is listed inside Precursor too, under
**Settings → Plugins**, where installing one is a button.

<PluginCatalog :plugins="plugins" />

## How the catalogue reaches the app

The list is **bundled with Precursor**, not fetched. It works with no network,
adds no failure states, phones nothing home, and every entry was reviewed in a
pull request before it shipped. The trade is that a newly listed plugin appears
in the next Precursor release.

An entry only ever supplies a **bare PyPI project name**. Anything expressing a
location — a URL, a path, a `@` requirement — is rejected when the catalogue is
loaded, so a merged pull request can't turn into code execution on someone
else's machine. Installing is otherwise unchanged: the same three gates apply
(loopback bind, a request addressed to it, and an explicit opt-in), and the
catalogue is a shortcut to a package name rather than a second way in.

## Getting listed

One file, one pull request — the page you are reading is generated from those
files. See [Submitting a plugin](/plugins/submitting).
