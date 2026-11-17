---
title: Submitting a plugin
description: Add one markdown file and open a pull request to get your plugin listed in Precursor.
---

# Submitting a plugin

Getting listed in the [catalogue](/plugins) is **one file and one pull
request**. The file is both the metadata Precursor reads and the plugin's
documentation page.

## 1. Publish the package

The catalogue installs from PyPI by name, so the distribution must exist there
and expose a `register(registry)` callable in the `precursor.plugins`
entry-point group. See the [plugin reference](/reference/plugins) and
[`precursor-kanban`](https://github.com/lrivallain/precursor-kanban).

Also attach the built wheel to each GitHub release and declare `repository`:
users whose index lags PyPI can then install from your releases, and anyone can
pick an older release.

## 2. Add `website/plugins/<your-plugin-id>.md`

The file name **must** be the plugin id (the entry-point name): it is the page
URL and the key matched against installed plugins.

```markdown
---
title: My plugin
description: One sentence, shown on the card in the app and on the site.
plugin: my-plugin
distribution: precursor-my-plugin
homepage: https://github.com/you/precursor-my-plugin
repository: https://github.com/you/precursor-my-plugin
author: you
license: MIT
tags: [github, notes]
contributes: [section, settings, mcp]
recommended: false
---

# My plugin

What it does, what it needs to work, and how to install it.
```

### Frontmatter

| Field | Required | What it is |
| --- | --- | --- |
| `title` | ✅ | Display name and page title. |
| `description` | ✅ | One-line summary on the catalogue card. |
| `plugin` | ✅ | Entry-point name: lowercase alphanumerics and dashes, **equal to the file name**. |
| `distribution` | ✅ | PyPI project name, a **bare name** (see below). |
| `homepage` | | `https://` URL of the source repository. |
| `repository` | | Exactly `https://github.com/<owner>/<repo>`. Enables installing from wheels attached to your GitHub releases. |
| `author` | | Maintainer. |
| `license` | | SPDX identifier, e.g. `MIT`. |
| `tags` | | Free-form keywords. |
| `contributes` | | Any of `section`, `settings`, `mcp`, `api`. |
| `recommended` | | Maintainers set this; leave it `false` or omit it. |

::: warning `distribution` is a bare name, and that is enforced
It is the only value handed to an installer, so it must match PEP 508's name
grammar: no URL, path, `@ …` requirement, extra or version specifier. The check
runs when the catalogue loads **and** in CI.
:::

### The body

The body becomes `/plugins/<id>` and ships in the app's offline docs. Write it
for someone deciding whether to install:

- **what it does**, in a paragraph;
- **what it needs**: token, scopes, external service, configured repository;
- **what it adds**: sections, MCP servers, API routes;
- **how to install it**, including any extra.

Keep deep documentation in your own repository and link to it.

## 3. Check it locally

```bash
# The catalogue page and your entry's page.
cd website && npm ci && npm run docs:dev

# The entry as the app parses it.
uv run --frozen python -c "
from precursor.backend.plugins.catalog import load_catalog
for entry in load_catalog(): print(entry)
"

# The validation CI runs.
uv run --frozen pytest tests/test_plugin_catalog.py
```

An entry that doesn't appear failed validation; the loader logs why and skips
it.

## 4. Open the pull request

Title it `catalog: add <your plugin>`. Nothing else changes: the catalogue page,
sidebar and in-app list are generated from your file.

Review checks the basics: the package resolves on PyPI, it is a Precursor
plugin, the repository is public, and the page explains it. It is not a code
audit, and listing is not an endorsement (only `recommended` entries are).

Once merged, the entry ships with the **next Precursor release**.

## Getting removed

Open a pull request deleting the file, or an issue. Abandoned plugins (package
no longer resolves, or no longer loads on a supported host) may be delisted.
Delisting removes the catalogue entry only; existing installs keep working.
