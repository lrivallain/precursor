---
title: Submitting a plugin
description: Add one markdown file and open a pull request to get your plugin listed in Precursor.
---

# Submitting a plugin

Getting listed in the [catalogue](/plugins) is **one file and one pull request**.
That file is both the metadata Precursor reads and the documentation page people
land on — there is no second place to keep in sync.

## 1. Publish the package first

The catalogue installs from PyPI by name, so the distribution has to exist there
and actually be a Precursor plugin: a package exposing a `register(registry)`
callable through the `precursor.plugins` entry-point group. If you haven't built
one yet, start with the [plugin contract](/features/plugins) and
[`precursor-kanban`](https://github.com/lrivallain/precursor-kanban) as the
worked example.

## 2. Add `website/plugins/<your-plugin-id>.md`

The file name **must** be your plugin id — the entry-point name — because it is
both the page URL and the key Precursor matches against installed plugins.

```markdown
---
title: My plugin
description: One sentence, shown on the card in the app and on the site.
plugin: my-plugin
distribution: precursor-my-plugin
homepage: https://github.com/you/precursor-my-plugin
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
| `title` | ✅ | Display name. Doubles as the page title. |
| `description` | ✅ | One-line summary, shown on the catalogue card. |
| `plugin` | ✅ | Entry-point name. Lowercase, alphanumeric and dashes, **equal to the file name**. |
| `distribution` | ✅ | PyPI project name. A **bare name** — see below. |
| `homepage` | — | `https://` URL of the source repository. |
| `author` | — | Who maintains it. |
| `license` | — | SPDX identifier, e.g. `MIT`. |
| `tags` | — | Free-form keywords for scanning the list. |
| `contributes` | — | Any of `section`, `settings`, `mcp`, `api`. |
| `recommended` | — | Maintainers set this. Leave it `false` (or omit it) in your PR. |

::: warning `distribution` is a bare name, and that is enforced
It is the one value handed to an installer, so it is validated against PEP 508's
name grammar: no URL, no path, no `@ …` requirement, no extra, no version
specifier. A catalogue that could say `pkg @ https://example.invalid/evil.whl`
would make a merged pull request into code execution on every machine that opens
the panel. The check runs when the catalogue loads **and** in CI, so a malformed
entry fails the build rather than shipping.
:::

### The body

The body becomes your page at `/plugins/<id>`, and it is bundled into the app's
own offline docs — so write it for someone deciding whether to install:

- **what it does**, in a paragraph, not a feature dump;
- **what it needs** — a token, a scope, an external service, a configured
  repository;
- **what it adds** — sections, MCP servers, API routes;
- **how to install it**, including any extra.

Keep it a page, not a manual. Deep documentation belongs in your own repository;
link to it.

## 3. Check it locally

The site and the app read the same file, so verify both:

```bash
# The catalogue page and your entry's own page.
cd website && npm install && npm run docs:dev

# The parsed entry, exactly as the app will see it.
uv run --frozen python -c "
from precursor.backend.plugins.catalog import load_catalog
for entry in load_catalog(): print(entry)
"

# The validation CI will run.
uv run --frozen pytest tests/test_plugin_catalog.py
```

If your entry doesn't appear, it failed validation — the loader logs the reason
and skips it rather than taking the panel down.

## 4. Open the pull request

Title it `catalog: add <your plugin>`. Nothing else needs to change: the
catalogue page, the sidebar entry and the in-app listing are all generated from
your file.

We'll look for the basics — the package resolves on PyPI, it really is a
Precursor plugin, the repository is public, and the page says what the plugin
does. We are not auditing your code, and listing is not an endorsement: only
entries the maintainers mark `recommended` carry one.

Once merged, the entry ships with the **next Precursor release** — the catalogue
is bundled rather than fetched, so it needs no network at runtime and no entry
appears that a human didn't review.

## Getting removed

Open a pull request deleting the file, or an issue if you'd rather we did. An
abandoned plugin — one whose package no longer resolves, or that no longer loads
against a supported host — may be delisted; that removes it from the catalogue
and nothing else. Nobody's install breaks, because the package is still on PyPI.
