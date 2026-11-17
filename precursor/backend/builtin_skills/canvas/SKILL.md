---
name: canvas
description: Illustrate a concept with a visual shown inline in the conversation (interactive HTML, draw.io diagram, Mermaid, SVG), using the canvas tools.
---

# Canvas: illustrate the conversation

A canvas is a visual that lives with this conversation and shows inline in your
reply. Use one when a picture, a diagram or a small simulation explains better
than prose: a mechanism that moves, a trade-off to explore with a slider, an
architecture, a sequence of steps, a comparison.

## The tools

- `write_canvas(conversation_id, name, kind, content)` creates or replaces a
  canvas. The conversation_id is given in your system prompt.
- `edit_canvas(conversation_id, name, edits)` changes part of one with exact
  `{"find", "replace"}` pairs. Prefer it for small changes ("make that box red",
  "add a step") instead of rewriting the whole canvas.
- `read_canvas` and `list_canvases` show what exists.
- Every write returns an `embed` block. Paste it, unchanged, into your reply
  where the visual belongs:

  ```canvas
  <conversation_id>/<name>.canvas.html
  ```

  Never paste the canvas source into the reply as well: the block shows it.

Give canvases short, descriptive names (`tcp-slow-start`, `auth-flow`). Several
canvases per conversation are fine; reusing a name replaces that canvas.

## Choosing the kind

| The user needs to... | kind |
| --- | --- |
| see something move, change a parameter, step through a process, explore data | `html` |
| look at, then edit, an architecture or a component map | `drawio` |
| follow a flow, a sequence of calls, states, a timeline, a mind map | `mermaid` |
| see one static illustration (an icon-like schema, a labelled figure) | `svg` |

When in doubt for a concept explanation, choose `html`: an interactive canvas
teaches more than a static one.

## kind "html": interactive canvases

A complete, self-contained HTML document (`<!doctype html>` ... `</html>`).

- Include the SDK first in `<head>`: `<script src="/canvas-sdk.js"></script>`.
- Vanilla HTML, CSS and JavaScript, with inline `<svg>` or `<canvas>`. Load a
  library from a CDN only when it saves real work (a charting or physics
  library) and the user is likely online.
- Theme: use the SDK's CSS variables so the canvas matches light and dark mode:
  `var(--pc-bg)`, `var(--pc-surface)`, `var(--pc-text)`, `var(--pc-muted)`,
  `var(--pc-border)`, `var(--pc-accent)`. Set `body { margin: 0; background:
  var(--pc-bg); color: var(--pc-text); font: 14px system-ui, sans-serif; }`.
- Size: design for a width of 480 to 900 px and let the height follow the
  content; the SDK resizes the inline frame for you. Avoid `100vh` layouts.
- Interaction: label every control, show the current value next to a slider,
  give a "Reset" button, and start in a state that already shows something.
  Animations get a play/pause control and stay smooth (`requestAnimationFrame`).
- Explain on the canvas itself with short captions; the reply around it holds
  the longer explanation.
- No `alert`, `prompt` or `confirm`; no forms that navigate away.

### The `window.precursor` SDK

| Call | What it does |
| --- | --- |
| `precursor.canvas` | `{ conversationId, name, path, kind }` of this canvas |
| `precursor.theme` / `precursor.onThemeChange(fn)` | `"light"` or `"dark"`, and a callback when it changes |
| `await precursor.state.load()` / `await precursor.state.save(obj)` | keep JSON between visits (positions, answers, settings) |
| `precursor.ask(text)` | put a question in the conversation's message box for the user to send |
| `precursor.download(filename, content, mime?)` | let the user save something the canvas produced (CSV, JSON, SVG) |
| `precursor.exportSvg(svgElement, filename)` | save an inline `<svg>` as a file |
| `await precursor.api(path, { method, body })` | call the Precursor API (`/api/...`) as the signed-in user |
| `precursor.resize()` | re-measure the height after a layout change the SDK could not see |

`precursor.ask` is how a canvas hands the conversation back: a "Explain this
step" button, a quiz answer, a chosen option.

## kind "drawio": editable diagrams

Uncompressed draw.io XML: `<mxfile><diagram name="Page-1"><mxGraphModel>...`.
Use clear containers for boundaries (a VPC, a cluster, a team), consistent
shapes per role, labels on edges that carry meaning, and a left-to-right or
top-to-bottom flow. The user can open it in the draw.io editor from the block.

## kind "mermaid": flows and sequences

Plain Mermaid source, no fence. Keep node labels short, quote labels with
punctuation (`A["Load balancer (L7)"]`), and split a diagram over about 25
nodes into two canvases.

## kind "svg": static illustrations

One `<svg>` element with a `viewBox` and no scripts. Use `currentColor` for
strokes and text so it reads in both themes, and readable font sizes (12 px
or more at the intended width).
