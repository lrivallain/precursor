---
title: Plugin catalogue
description: Plugins that extend Precursor, and how to get yours listed here.
---

<script setup>
import { data as plugins } from "../.vitepress/catalog.data.mts";
</script>

# Plugin catalogue

[Plugins](/features/plugins) are Python packages that add a section, API
routes, settings and MCP tools to Precursor. These are the ones we know about;
the same list is in the app under **Settings → Plugins → Available**.

<PluginCatalog :plugins="plugins" />

The list is **bundled with each Precursor release**, not fetched, and entries
can only name a bare PyPI project. Installing from it goes through the same
[gates](/reference/plugins#why-installing-is-opt-in) as typing a package name.

**Get yours listed**: one file, one pull request. See
[Submitting a plugin](/plugins/submitting).
