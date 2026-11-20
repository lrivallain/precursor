---
title: Canvases
---

# Canvases

A **canvas** is a visual the assistant draws for a conversation and shows right
in its reply: an interactive page you can play with, a draw.io diagram you can
edit, a Mermaid flow or an SVG illustration. Ask "show me how TCP slow start
reacts to a loss" and the answer comes with a simulation and a slider.

## What a canvas can be

| Kind | Good for | In the conversation |
| --- | --- | --- |
| **Interactive** (HTML) | mechanisms that move, parameters to explore, step-by-step walkthroughs, quizzes, small charts | a live page, sized to its content |
| **draw.io** | architectures, component maps, anything you want to keep editing | the draw.io editor, saving as you go |
| **Mermaid** | flows, sequences, states, timelines, mind maps | the rendered diagram, with its source a click away |
| **SVG** | one static illustration | the rendered image |

Each canvas has a header to **reload** it, **download** it, open it in the
**Canvas workspace**, or view it **full screen** (<kbd>Esc</kbd> to leave).

## How it works

- Canvases are files in a built-in workspace named **Canvas**, one folder per
  conversation (named after its id; hover a folder to see its topic or chat's
  title), several canvases per conversation. In
  **Files** it is listed right after **Definitions**, with its own colour, and
  can't be removed. Browse, edit or preview canvases there; `.mmd` and `.svg`
  files get a rendered preview too.
- A canvas the assistant writes is attached to the conversation as an
  [artifact](/features/workspaces), so it is listed with the conversation's
  files and the assistant knows it exists in later turns. Ask it to change one
  ("make the red path dashed", "add a retry step"): it edits the canvas in
  place rather than drawing a new one.
- The reply shows a canvas with a fenced block that names its path:

  ````markdown
  ```canvas
  0f8fad5b-d9cb-469f-a165-70867728950e/slow-start.canvas.html
  ```
  ````

- Deleting a conversation deletes its canvas folder.

The assistant draws with the canvas tools of the built-in `precursor`
[MCP server](/features/mcp#as-a-server-exposing-your-conversations)
(`canvas_guide`, `list_canvases`, `read_canvas`, `write_canvas`, `edit_canvas`),
guided by the built-in **`/canvas`** [skill](/features/skills-memory#skills).
The **Canvases** section of **Settings → MCP servers → Precursor capabilities**
is **on by default**, because its tools only write to the Canvas workspace; turn
it off to stop conversations from drawing. The tools are only offered where the
`precursor` server is enabled for the conversation.

## Interactive canvases and the SDK

An interactive canvas is a self-contained HTML page. It runs **on Precursor's
own origin**, on purpose: through the small `window.precursor` SDK
(`<script src="/canvas-sdk.js"></script>`) it follows the app's theme, keeps
state between visits, and can hand something back to the conversation.

| Call | What it does |
| --- | --- |
| `precursor.canvas` | `{ conversationId, name, path, kind }` of this canvas |
| `precursor.theme`, `precursor.onThemeChange(fn)` | `"light"` or `"dark"`, and changes to it; the colours are CSS variables (`--pc-bg`, `--pc-surface`, `--pc-text`, `--pc-muted`, `--pc-border`, `--pc-accent`) |
| `precursor.state.load()`, `precursor.state.save(obj)` | JSON kept beside the canvas (`.state/<name>.json`, 500 KB at most) |
| `precursor.ask(text)` | put text in the conversation's message box, for you to send; from the Canvas workspace, it opens the canvas's conversation beside it first |
| `precursor.download(name, content, mime?)`, `precursor.exportSvg(svg, name)` | save something the canvas produced |
| `precursor.api(path, { method, body })` | call the [Precursor API](/reference/api) |
| `precursor.resize()` | re-measure the frame after a layout change |

::: warning A canvas acts as you
Because it shares the app's origin, a canvas page can call the whole Precursor
API, like any page of the app. That is what lets a canvas drive Precursor; it
also means you should treat a canvas like code you run. Canvases written by the
assistant come from your own conversation; be careful with HTML copied in from
elsewhere.
:::

The same page opens on its own at `/raw/canvas/<conversation id>/<file>` (the
download link), where the theme follows your system.
