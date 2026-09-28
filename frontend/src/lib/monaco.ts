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
