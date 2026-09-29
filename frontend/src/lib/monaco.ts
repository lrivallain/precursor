// Monaco setup: workers, languages, and a theme that follows the app's.
//
// Only the lazily loaded editor components import this module, so Monaco
// stays in its own chunk and loads the first time a file is edited.

import * as monaco from "monaco-editor/editor";
import "monaco-editor/features/register.all";
import "monaco-editor/languages/definitions/css/register";
import "monaco-editor/languages/definitions/html/register";
import "monaco-editor/languages/definitions/ini/register";
import "monaco-editor/languages/definitions/javascript/register";
import "monaco-editor/languages/definitions/markdown/register";
import "monaco-editor/languages/definitions/python/register";
import "monaco-editor/languages/definitions/restructuredtext/register";
import "monaco-editor/languages/definitions/shell/register";
import "monaco-editor/languages/definitions/typescript/register";
import "monaco-editor/languages/definitions/xml/register";
import "monaco-editor/languages/definitions/yaml/register";
// JSON and YAML are the only languages with a language service (validation,
// completion, formatting): the others would each bring a multi-megabyte worker.
import "monaco-editor/languages/features/json/register";
import { configureMonacoYaml } from "monaco-yaml";
import { findConflicts, resolution } from "./conflicts";
import type { ConflictChoice } from "./conflicts";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import JsonWorker from "monaco-editor/languages/features/json/json.worker.js?worker";
import YamlWorker from "./yaml.worker?worker";

export { monaco };

self.MonacoEnvironment = {
  getWorker(_moduleId: string, label: string): Worker {
    if (label === "json") return new JsonWorker();
    if (label === "yaml") return new YamlWorker();
    return new EditorWorker();
  },
};

// monaco-yaml still asks for its worker the pre-0.55 way ({label, createData});
// 0.57's createWebWorker takes the Worker itself. This is the same bridge Monaco
// uses for its own JSON worker (internal/common/workers.js): two messages that
// hand the worker its creation data, then the regular RPC setup.
const yamlHost = {
  ...monaco,
  editor: {
    ...monaco.editor,
    createWebWorker(opts: {
      createData?: unknown;
      host?: Record<string, (...args: unknown[]) => unknown>;
      keepIdleModels?: boolean;
    }) {
      const worker = new YamlWorker();
      worker.postMessage("ignore");
      worker.postMessage(opts.createData);
      return monaco.editor.createWebWorker({
        worker,
        host: opts.host,
        keepIdleModels: opts.keepIdleModels,
      });
    },
  },
};

export const monacoYaml = configureMonacoYaml(
  yamlHost as unknown as Parameters<typeof configureMonacoYaml>[0],
  { enableSchemaRequest: false, format: { enable: true } },
);

const DEFINITION_KINDS = ["agent", "workflow", "summary"] as const;
let definitionSchemas: Promise<void> | null = null;

// Keywords whose value is data, not a subschema: a "title" in there is a value.
const DATA_KEYWORDS = new Set(["default", "examples", "const", "enum"]);
const SCHEMA_MAPS = new Set(["properties", "$defs", "definitions", "patternProperties"]);

/**
 * A JSON Schema without its `title` keywords. yaml-language-server's hover
 * prints the title of every schema matching a node, joined — "Precursor workflow
 * definition || Precursor workflow definition" — so the hover keeps just the
 * descriptions. A property *named* `title` is left alone.
 */
function withoutTitles(schema: unknown): unknown {
  if (Array.isArray(schema)) return schema.map(withoutTitles);
  if (!schema || typeof schema !== "object") return schema;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(schema)) {
    if (key === "title" && typeof value === "string") continue;
    if (DATA_KEYWORDS.has(key)) out[key] = value;
    else if (SCHEMA_MAPS.has(key) && value && typeof value === "object")
      out[key] = Object.fromEntries(
        Object.entries(value).map(([name, sub]) => [name, withoutTitles(sub)]),
      );
    else out[key] = withoutTitles(value);
  }
  return out;
}

/**
 * Give `*.agent.yaml` / `*.workflow.yaml` / `*.summary.yaml` their JSON Schema: completion,
 * hovers, and unknown keys flagged as you type. Fetched once, the first time
 * such a file is opened; the backend serves the same schema as `docs/schemas/`.
 */
export function loadDefinitionSchemas(
  load: (kind: (typeof DEFINITION_KINDS)[number]) => Promise<Record<string, unknown>>,
): Promise<void> {
  definitionSchemas ??= Promise.all(DEFINITION_KINDS.map((kind) => load(kind)))
    .then((loaded) => loaded.map((schema) => withoutTitles(schema) as object))
    .then((schemas) =>
      monacoYaml.update({
        schemas: DEFINITION_KINDS.flatMap((kind, i) => [
          {
            uri: new URL(`/api/definitions/schema/${kind}`, window.location.origin).href,
            fileMatch: [`**/*.${kind}.yaml`],
            schema: schemas[i],
          },
          // Where the documented modeline (`$schema=../../../schemas/…`)
          // resolves for a file in the definitions workspace: the schema is
          // known, not "unable to load".
          { uri: `file:///schemas/${kind}.schema.json`, fileMatch: [], schema: schemas[i] },
        ]),
      }),
    )
    .catch(() => {
      // Retry on the next definition file rather than never.
      definitionSchemas = null;
    });
  return definitionSchemas;
}

// --- Merge conflicts ----------------------------------------------------------
// A model marked as conflicted gets "Accept current | incoming | both" above
// each conflict block (one CodeLens provider for every language) and tinted
// block regions (decorations, applied by the editor component).

const conflicted = new Set<string>();
const lensesChanged = new monaco.Emitter<monaco.languages.CodeLensProvider>();
const ACCEPT_COMMAND = "precursor.acceptConflict";
const CHOICES: [ConflictChoice, string][] = [
  ["current", "Accept current (yours)"],
  ["incoming", "Accept incoming (theirs)"],
  ["both", "Accept both"],
];

const conflictLenses: monaco.languages.CodeLensProvider = {
  onDidChange: lensesChanged.event,
  provideCodeLenses(model) {
    if (!conflicted.has(model.uri.toString())) return { lenses: [], dispose() {} };
    const lenses = findConflicts(model.getLinesContent()).flatMap((block, index) =>
      CHOICES.map(([choice, title]) => ({
        range: new monaco.Range(block.start, 1, block.start, 1),
        command: { id: ACCEPT_COMMAND, title, arguments: [model.uri.toString(), index, choice] },
      })),
    );
    return { lenses, dispose() {} };
  },
};
monaco.languages.registerCodeLensProvider("*", conflictLenses);

monaco.editor.registerCommand(
  ACCEPT_COMMAND,
  (_accessor: unknown, uri: string, index: number, choice: ConflictChoice) => {
    const model = monaco.editor.getModel(monaco.Uri.parse(uri));
    if (model) acceptConflict(model, index, choice);
  },
);

/** Replace one conflict block, markers and all, with the chosen side(s); undoable. */
export function acceptConflict(
  model: monaco.editor.ITextModel,
  index: number,
  choice: ConflictChoice,
): void {
  const lines = model.getLinesContent();
  const block = findConflicts(lines)[index];
  if (!block) return;
  const kept = resolution(lines, block, choice);
  const eol = model.getEOL();
  const last = model.getLineCount();
  let range: monaco.Range;
  let text: string;
  if (block.end < last) {
    // Whole lines, through the start of the line after the block.
    range = new monaco.Range(block.start, 1, block.end + 1, 1);
    text = kept.length ? kept.join(eol) + eol : "";
  } else if (kept.length || block.start === 1) {
    range = new monaco.Range(block.start, 1, block.end, model.getLineMaxColumn(block.end));
    text = kept.join(eol);
  } else {
    // The block ends the file and nothing is kept: take the line break before it too.
    range = new monaco.Range(
      block.start - 1,
      model.getLineMaxColumn(block.start - 1),
      block.end,
      model.getLineMaxColumn(block.end),
    );
    text = "";
  }
  model.pushStackElement();
  model.pushEditOperations([], [{ range, text }], () => null);
  model.pushStackElement();
}

/** Show (or stop showing) the conflict lenses on a model. */
export function markConflicted(model: monaco.editor.ITextModel, on: boolean): void {
  const key = model.uri.toString();
  if (on) conflicted.add(key);
  else conflicted.delete(key);
  lensesChanged.fire(conflictLenses);
}

/** Tinted regions for each block: yours, theirs, and the marker lines. */
export function conflictDecorations(
  model: monaco.editor.ITextModel,
): monaco.editor.IModelDeltaDecoration[] {
  const whole = (from: number, to: number, className: string) =>
    to < from
      ? []
      : [{ range: new monaco.Range(from, 1, to, 1), options: { isWholeLine: true, className } }];
  return findConflicts(model.getLinesContent()).flatMap((b) => [
    ...whole(b.start, b.start, "precursor-conflict-marker"),
    ...whole(b.start + 1, b.middle - 1, "precursor-conflict-ours"),
    ...whole(b.middle, b.middle, "precursor-conflict-marker"),
    ...whole(b.middle + 1, b.end - 1, "precursor-conflict-theirs"),
    ...whole(b.end, b.end, "precursor-conflict-marker"),
  ]);
}

/** Refresh the lenses after an edit (the blocks may have moved or gone). */
export function refreshConflictLenses(): void {
  lensesChanged.fire(conflictLenses);
}

const LANGUAGES: [suffix: string, language: string][] = [
  [".md", "markdown"],
  [".markdown", "markdown"],
  [".yaml", "yaml"],
  [".yml", "yaml"],
  [".json", "json"],
  [".html", "html"],
  [".htm", "html"],
  [".css", "css"],
  [".js", "javascript"],
  [".ts", "typescript"],
  [".py", "python"],
  [".sh", "shell"],
  [".drawio", "xml"],
  [".drawio.xml", "xml"],
  [".xml", "xml"],
  [".toml", "ini"],
  [".ini", "ini"],
  [".env", "ini"],
  [".rst", "restructuredtext"],
];

export function languageFor(path: string): string {
  const lower = path.toLowerCase();
  const name = lower.slice(lower.lastIndexOf("/") + 1);
  if (name === ".env" || name.startsWith(".env.")) return "ini";
  return LANGUAGES.find(([suffix]) => lower.endsWith(suffix))?.[1] ?? "plaintext";
}

/** Prose is easier to edit wrapped; code keeps its lines. */
export function wrapsLines(path: string): boolean {
  return ["markdown", "restructuredtext", "plaintext"].includes(languageFor(path));
}

// --- Theme -------------------------------------------------------------------

const THEME = "precursor";

function cssVar(style: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = style.getPropertyValue(name).trim();
  return /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
}

// Monaco themes take literal colours, so the app's tokens are read at the time
// and the theme is redefined whenever `.dark` flips on <html>.
function applyTheme(): void {
  const root = document.documentElement;
  const dark = root.classList.contains("dark");
  const style = getComputedStyle(root);
  const bg = cssVar(style, "--bg", dark ? "#0b0d10" : "#ffffff");
  const surface = cssVar(style, "--surface", dark ? "#15181d" : "#f7f7f8");
  const border = cssVar(style, "--border", dark ? "#2a2f37" : "#e5e7eb");
  const text = cssVar(style, "--text", dark ? "#e6e8eb" : "#111827");
  const muted = cssVar(style, "--muted", dark ? "#8a93a0" : "#6b7280");
  const accent = cssVar(style, "--accent", dark ? "#60a5fa" : "#2563eb");
  monaco.editor.defineTheme(THEME, {
    base: dark ? "vs-dark" : "vs",
    inherit: true,
    rules: [],
    colors: {
      "editor.background": bg,
      "editor.foreground": text,
      "editorGutter.background": bg,
      "editorLineNumber.foreground": muted,
      "editorLineNumber.activeForeground": text,
      "editor.lineHighlightBackground": surface,
      "editor.lineHighlightBorder": surface,
      "editorCursor.foreground": accent,
      "editor.selectionBackground": `${accent}40`,
      "editor.inactiveSelectionBackground": `${accent}26`,
      "editorWidget.background": surface,
      "editorWidget.border": border,
      "editorHoverWidget.background": surface,
      "editorHoverWidget.border": border,
      "editorSuggestWidget.background": surface,
      "editorSuggestWidget.border": border,
      "focusBorder": accent,
      "scrollbarSlider.background": `${muted}33`,
      "scrollbarSlider.hoverBackground": `${muted}55`,
      "scrollbarSlider.activeBackground": `${muted}77`,
      "diffEditor.border": border,
    },
  });
  monaco.editor.setTheme(THEME);
}

applyTheme();
new MutationObserver(applyTheme).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ["class"],
});

export const EDITOR_FONT = {
  fontFamily: '"JetBrains Mono", Menlo, Consolas, monospace',
  fontSize: 13,
};
