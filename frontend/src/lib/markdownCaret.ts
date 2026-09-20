interface HNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
}

const SOURCE_SELECTOR = "[data-md-source-start]";
const CONTROLS = "a, button, input, label, textarea, select, [role=button], svg, canvas, img";

/** Annotate only editable Markdown, before highlighting replaces its text nodes. */
export function makeMarkdownCaretRehype(sourceOffset: (offset: number) => number) {
  function properties(node: HNode, kind = "text"): Record<string, unknown> | null {
    const start = node.position?.start.offset;
    const end = node.position?.end.offset;
    return start == null || end == null ? null : {
      "data-md-source-start": sourceOffset(start),
      "data-md-source-end": sourceOffset(end),
      "data-md-source-kind": kind,
    };
  }
  function walk(node: HNode): void {
    if (!node.children) return;
    node.children = node.children.map((child) => {
      if (child.type === "element" && child.tagName === "code") {
        // Keep code text intact for the syntax highlighter; its parent retains
        // the source range even when highlighting splits it into many spans.
        const range = properties(child, node.tagName === "pre" ? "block-code" : "inline-code");
        if (range) child.properties = { ...child.properties, ...range };
        return child;
      }
      if (child.type === "text") {
        const range = properties(child);
        if (range) return { type: "element", tagName: "span", properties: range, children: [child] };
      }
      walk(child);
      return child;
    });
  }
  return function rehypeMarkdownCaret() {
    return walk;
  };
}

function decodedText(raw: string, inlineCode = false): { text: string; boundaries: number[] } {
  let text = "";
  const boundaries = [0];
  const decoder = document.createElement("textarea");
  for (let i = 0; i < raw.length;) {
    let token = raw[i];
    let width = 1;
    if (raw[i] === "\r" || raw[i] === "\n") {
      width = raw.slice(i, i + 2) === "\r\n" ? 2 : 1;
      token = inlineCode ? " " : "\n";
    } else if (!inlineCode && raw[i] === "\\" && /[!-/:-@[-`{-~]/.test(raw[i + 1] ?? "")) {
      token = raw[i + 1];
      width = 2;
    } else if (!inlineCode && raw[i] === "&") {
      const entity = raw.slice(i).match(/^&(?:#x[\da-f]+|#\d+|[a-z][a-z\d]+);/i)?.[0];
      if (entity) {
        // Only an entity token is parsed, never user-supplied HTML.
        decoder.innerHTML = entity;
        token = decoder.value;
        width = entity.length;
      }
    }
    i += width;
    text += token;
    for (let unit = 0; unit < token.length; unit++) boundaries.push(i);
  }
  return { text, boundaries };
}

function offsetInSource(raw: string, text: string, offset: number, kind: string): number {
  if (kind === "block-code") {
    const header = raw.match(/^[ \t]{0,3}(?:`{3,}|~{3,})[^\r\n]*(?:\r\n|\r|\n)/)?.[0].length ?? 0;
    const prefix = text.slice(0, offset).split("\n");
    const lineIndex = prefix.length - 1;
    const lines = raw.slice(header).match(/[^\r\n]*(?:\r\n|\r|\n|$)/g) ?? [];
    const line = lines[lineIndex] ?? "";
    const visibleLine = text.split("\n")[lineIndex] ?? "";
    return header + lines.slice(0, lineIndex).reduce((sum, value) => sum + value.length, 0)
      + Math.max(0, line.indexOf(visibleLine)) + prefix[lineIndex].length;
  }
  const delimiter = kind === "inline-code" ? raw.match(/^`+/)?.[0].length ?? 0 : 0;
  const decoded = decodedText(delimiter ? raw.slice(delimiter, -delimiter) : raw, kind === "inline-code");
  if (kind === "inline-code" && decoded.text.startsWith(" ") && decoded.text.endsWith(" ")
    && /[^ ]/.test(decoded.text)) {
    decoded.text = decoded.text.slice(1, -1);
    decoded.boundaries = decoded.boundaries.slice(1, -1);
  }
  const exact = decoded.text.indexOf(text);
  if (exact >= 0) return delimiter + (decoded.boundaries[exact + offset] ?? raw.length);

  // Continuation-line indentation can disappear during Markdown parsing.
  let cursor = 0;
  for (let i = 0; i < offset; i++) {
    while (cursor < decoded.text.length && decoded.text[cursor] !== text[i]
      && /\s/.test(decoded.text[cursor])) cursor++;
    if (decoded.text[cursor] !== text[i]) break;
    cursor++;
  }
  return delimiter + (decoded.boundaries[cursor] ?? raw.length);
}

type CaretDocument = Document & {
  caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
  caretRangeFromPoint?: (x: number, y: number) => Range | null;
};

export function markdownCaretAtPoint(root: HTMLElement, event: MouseEvent, source: string): number | null {
  const target = event.target instanceof Element ? event.target : null;
  if (!target || target.closest(CONTROLS) || !target.closest(SOURCE_SELECTOR)) return null;
  const doc = root.ownerDocument as CaretDocument;
  const position = doc.caretPositionFromPoint?.(event.clientX, event.clientY);
  const range = position ? null : doc.caretRangeFromPoint?.(event.clientX, event.clientY);
  const selection = doc.getSelection();
  const node = position?.offsetNode ?? range?.startContainer ?? selection?.anchorNode;
  const offset = position?.offset ?? range?.startOffset ?? selection?.anchorOffset;
  if (!node || offset == null || !root.contains(node)) return null;
  const element = (node instanceof Element ? node : node.parentElement)?.closest<HTMLElement>(SOURCE_SELECTOR);
  if (!element || !root.contains(element)) return null;
  const start = Number(element.dataset.mdSourceStart);
  const end = Number(element.dataset.mdSourceEnd);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  const prefix = doc.createRange();
  prefix.selectNodeContents(element);
  prefix.setEnd(node, offset);
  const local = offsetInSource(
    source.slice(start, end), element.textContent ?? "", prefix.toString().length,
    element.dataset.mdSourceKind ?? "text",
  );
  return Math.max(start, Math.min(end, start + local));
}

/** Reveal a Markdown source offset, including when a long paragraph wraps. */
export function focusTextareaAt(textarea: HTMLTextAreaElement, offset: number): void {
  const caret = Math.max(0, Math.min(offset, textarea.value.length));
  textarea.focus({ preventScroll: true });
  textarea.setSelectionRange(caret, caret);
  const style = getComputedStyle(textarea);
  const mirror = document.createElement("div");
  Object.assign(mirror.style, {
    position: "absolute", left: "-10000px", top: "0", visibility: "hidden",
    boxSizing: "border-box", width: `${textarea.clientWidth}px`, padding: style.padding,
    fontFamily: style.fontFamily, fontSize: style.fontSize, fontWeight: style.fontWeight,
    fontStyle: style.fontStyle, lineHeight: style.lineHeight, letterSpacing: style.letterSpacing,
    tabSize: style.tabSize, whiteSpace: "pre-wrap", overflowWrap: "break-word",
  });
  const marker = document.createElement("span");
  marker.textContent = textarea.value.slice(caret) || "\u200b";
  mirror.append(document.createTextNode(textarea.value.slice(0, caret)), marker);
  document.body.append(mirror);
  textarea.scrollTop = Math.max(0, marker.offsetTop - textarea.clientHeight / 2);
  mirror.remove();
}
