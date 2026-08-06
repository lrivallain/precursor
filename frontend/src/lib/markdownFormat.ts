/**
 * Pure text transforms that power the Markdown formatting toolbar and keyboard
 * shortcuts. Each function takes the current textarea state (value + selection)
 * and returns the next state, so the caller stays in charge of applying it to
 * the DOM and restoring the selection. Keeping this logic side-effect free makes
 * it trivial to reason about (and to unit test) without a real textarea.
 */

export type MarkdownAction =
  | "bold"
  | "italic"
  | "strikethrough"
  | "code"
  | "link"
  | "heading"
  | "quote"
  | "unordered-list"
  | "ordered-list";

export interface SelectionState {
  value: string;
  selectionStart: number;
  selectionEnd: number;
}

/** Inline markers keyed by the toggling actions that use `toggleWrap`. */
const INLINE_MARKERS: Record<"bold" | "italic" | "strikethrough" | "code", string> = {
  bold: "**",
  italic: "_",
  strikethrough: "~~",
  code: "`",
};

/**
 * Toggle an inline wrap (bold/italic/…) around the selection. The marker is
 * removed when it already hugs the selection — whether the markers sit inside
 * the selected span or just outside it — otherwise it is added. With an empty
 * selection the caret is parked between the freshly inserted markers.
 */
function toggleWrap(state: SelectionState, marker: string): SelectionState {
  const { value, selectionStart: start, selectionEnd: end } = state;
  const before = value.slice(0, start);
  const selected = value.slice(start, end);
  const after = value.slice(end);
  const len = marker.length;

  // Markers already inside the selection → unwrap them.
  if (
    selected.length >= len * 2 &&
    selected.startsWith(marker) &&
    selected.endsWith(marker)
  ) {
    const inner = selected.slice(len, selected.length - len);
    return {
      value: before + inner + after,
      selectionStart: start,
      selectionEnd: start + inner.length,
    };
  }

  // Markers wrapping the selection from the outside → strip them.
  if (before.endsWith(marker) && after.startsWith(marker)) {
    return {
      value: before.slice(0, before.length - len) + selected + after.slice(len),
      selectionStart: start - len,
      selectionEnd: end - len,
    };
  }

  // Otherwise wrap the selection (or drop an empty pair at the caret).
  if (selected.length === 0) {
    const caret = start + len;
    return {
      value: before + marker + marker + after,
      selectionStart: caret,
      selectionEnd: caret,
    };
  }
  return {
    value: before + marker + selected + marker + after,
    selectionStart: start + len,
    selectionEnd: end + len,
  };
}

/**
 * Turn the selection into a Markdown link. A selected URL is dropped into the
 * target and the caret lands in the (empty) label; otherwise the selected text
 * becomes the label and the `url` placeholder is selected so it can be typed
 * over immediately.
 */
function applyLink(state: SelectionState): SelectionState {
  const { value, selectionStart: start, selectionEnd: end } = state;
  const before = value.slice(0, start);
  const selected = value.slice(start, end);
  const after = value.slice(end);

  const looksLikeUrl = /^(https?:\/\/|mailto:)\S+$/i.test(selected.trim());
  if (looksLikeUrl) {
    const snippet = `[](${selected.trim()})`;
    const caret = start + 1; // between the brackets, ready for the label
    return { value: before + snippet + after, selectionStart: caret, selectionEnd: caret };
  }

  const placeholder = "url";
  const snippet = `[${selected}](${placeholder})`;
  const urlStart = start + 1 + selected.length + 2; // past `[label](`
  return {
    value: before + snippet + after,
    selectionStart: urlStart,
    selectionEnd: urlStart + placeholder.length,
  };
}

interface LinePrefixSpec {
  /** Builds the prefix to add for the nth selected line (0-based). */
  add: (index: number) => string;
  /** Matches an existing prefix so it can be detected and stripped. */
  strip: RegExp;
}

const LINE_SPECS: Record<
  "heading" | "quote" | "unordered-list" | "ordered-list",
  LinePrefixSpec
> = {
  heading: { add: () => "# ", strip: /^#{1,6} +/ },
  quote: { add: () => "> ", strip: /^> ?/ },
  "unordered-list": { add: () => "- ", strip: /^[-*+] +/ },
  "ordered-list": { add: (i) => `${i + 1}. `, strip: /^\d+\. +/ },
};

/**
 * Toggle a line-level prefix (heading/quote/list) across every line the
 * selection touches. If all touched lines already carry the prefix it is
 * removed; otherwise the prefix is applied to each line. The whole rewritten
 * block is re-selected so the toggle can be repeated or chained.
 */
function toggleLinePrefix(state: SelectionState, spec: LinePrefixSpec): SelectionState {
  const { value, selectionStart: start, selectionEnd: end } = state;
  const blockStart = value.lastIndexOf("\n", start - 1) + 1;
  let blockEnd = value.indexOf("\n", end);
  if (blockEnd === -1) blockEnd = value.length;

  const lines = value.slice(blockStart, blockEnd).split("\n");
  const allPrefixed = lines.every((line) => spec.strip.test(line));

  const rewritten = lines
    .map((line, i) =>
      allPrefixed ? line.replace(spec.strip, "") : spec.add(i) + line,
    )
    .join("\n");

  return {
    value: value.slice(0, blockStart) + rewritten + value.slice(blockEnd),
    selectionStart: blockStart,
    selectionEnd: blockStart + rewritten.length,
  };
}

/** Apply a formatting `action` to the given selection state. */
export function applyMarkdown(action: MarkdownAction, state: SelectionState): SelectionState {
  switch (action) {
    case "bold":
    case "italic":
    case "strikethrough":
    case "code":
      return toggleWrap(state, INLINE_MARKERS[action]);
    case "link":
      return applyLink(state);
    case "heading":
    case "quote":
    case "unordered-list":
    case "ordered-list":
      return toggleLinePrefix(state, LINE_SPECS[action]);
  }
}