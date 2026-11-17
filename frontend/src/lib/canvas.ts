// Conversation canvases: files in the built-in "canvas" workspace, one folder per
// conversation (services/canvases.py). A reply shows one with a ```canvas fence
// whose body is the canvas path, `<conversation_id>/<name><suffix>`.

export const CANVAS_WORKSPACE_SLUG = "canvas";

export type CanvasKind = "html" | "drawio" | "mermaid" | "svg";

// Longest first: `.canvas.html` must not read as a plain `.html` file.
const SUFFIXES: [CanvasKind, string][] = [
  ["html", ".canvas.html"],
  ["drawio", ".drawio"],
  ["mermaid", ".mmd"],
  ["svg", ".svg"],
];

const CONVERSATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const FILE_NAME = /^[a-z0-9][a-z0-9-]*$/;

export interface CanvasRef {
  path: string;
  conversationId: string;
  name: string;
  kind: CanvasKind;
}

export function canvasKind(path: string): CanvasKind | null {
  const lower = path.toLowerCase();
  for (const [kind, suffix] of SUFFIXES) if (lower.endsWith(suffix)) return kind;
  return null;
}

/** Read a ```canvas fence body; null when it isn't a canvas path. */
export function parseCanvasRef(body: string): CanvasRef | null {
  const path = body.trim().replace(/^\/+/, "");
  const parts = path.split("/");
  if (parts.length !== 2) return null;
  const [conversationId, file] = parts;
  if (!CONVERSATION_ID.test(conversationId)) return null;
  const kind = canvasKind(file);
  if (!kind) return null;
  const suffix = SUFFIXES.find(([k]) => k === kind)![1];
  const name = file.slice(0, -suffix.length);
  if (!FILE_NAME.test(name)) return null;
  return { path, conversationId, name, kind };
}

/** Where `/raw` serves the canvas (also the base for its relative links). */
export function canvasRawUrl(path: string, version = 0): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  return `/raw/${CANVAS_WORKSPACE_SLUG}/${encoded}${version ? `?v=${version}` : ""}`;
}

export function canvasFileName(ref: CanvasRef): string {
  return ref.path.split("/").pop() ?? ref.name;
}

// Messages a canvas page posts to the block that frames it (canvas-sdk.js).
export type CanvasMessage =
  | { type: "precursor:canvas-resize"; height: number }
  | { type: "precursor:canvas-ask"; text: string };

export const MIN_FRAME_HEIGHT = 120;
export const MAX_FRAME_HEIGHT = 2000;

export function readCanvasMessage(data: unknown): CanvasMessage | null {
  if (!data || typeof data !== "object") return null;
  const msg = data as Record<string, unknown>;
  if (msg.type === "precursor:canvas-resize" && typeof msg.height === "number") {
    const height = Math.round(Math.min(MAX_FRAME_HEIGHT, Math.max(MIN_FRAME_HEIGHT, msg.height)));
    return Number.isFinite(height) ? { type: msg.type, height } : null;
  }
  if (msg.type === "precursor:canvas-ask" && typeof msg.text === "string" && msg.text.trim()) {
    return { type: msg.type, text: msg.text.slice(0, 4000) };
  }
  return null;
}

/**
 * Put text in the conversation's message box without sending it. The composer
 * is a controlled React textarea, so the value goes through the native setter
 * and an input event, which React reads as the user typing.
 */
export function fillComposer(text: string, near?: Element | null): boolean {
  const scope = near?.closest("[data-conversation]") ?? document;
  const box =
    scope.querySelector<HTMLTextAreaElement>("textarea[data-composer]") ??
    document.querySelector<HTMLTextAreaElement>("textarea[data-composer]");
  if (!box) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  const next = box.value.trim() ? `${box.value.trimEnd()}\n\n${text}` : text;
  setter?.call(box, next);
  box.dispatchEvent(new Event("input", { bubbles: true }));
  box.focus();
  return true;
}
