import { createContext, useContext, type ReactNode } from "react";

/**
 * The active "find" term propagated to every Markdown body rendered under the
 * provider. An empty string means no highlighting. It is set when a content
 * search hit is opened from the command palette and hydrated from the `?q=`
 * URL param on load / back-forward, so a shared link re-highlights.
 */
const SearchHighlightContext = createContext<string>("");

export function SearchHighlightProvider({
  term,
  children,
}: {
  term: string;
  children: ReactNode;
}) {
  return (
    <SearchHighlightContext.Provider value={term}>
      {children}
    </SearchHighlightContext.Provider>
  );
}

/** The current highlight term (trimmed by the caller as needed). */
export function useSearchHighlight(): string {
  return useContext(SearchHighlightContext);
}

/**
 * Highlight the active term inside a plain-text string (for surfaces that render
 * text directly rather than through Markdown — e.g. agent prompts, live meeting
 * insights). Returns the text untouched when no term is set. Matching is
 * case-insensitive substring search.
 */
export function HighlightedText({ text }: { text: string }): ReactNode {
  const term = useSearchHighlight().trim();
  if (!term) return text;
  const lower = text.toLowerCase();
  const lq = term.toLowerCase();
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  for (;;) {
    const idx = lower.indexOf(lq, i);
    if (idx < 0) {
      out.push(text.slice(i));
      break;
    }
    if (idx > i) out.push(text.slice(i, idx));
    out.push(
      <mark key={key++} className="search-hl">
        {text.slice(idx, idx + term.length)}
      </mark>,
    );
    i = idx + term.length;
  }
  return out;
}

// ---- rehype plugin ------------------------------------------------------

// A minimal hast node shape — enough to walk and rewrite text nodes without
// pulling in the full `hast`/`unist` type packages.
interface HNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HNode[];
}

// Never rewrite text inside these: code/pre must keep their syntax highlighting
// intact, and re-descending into a <mark> would double-wrap a match.
const SKIP_TAGS = new Set(["code", "pre", "mark", "script", "style"]);

/**
 * Build a rehype transformer that wraps every case-insensitive occurrence of
 * `query` in the rendered text with `<mark class="search-hl">`, leaving code
 * blocks untouched. A blank query yields a no-op transformer.
 *
 * Matching uses plain substring search (not regex), so terms with special
 * characters are matched literally.
 */
export function makeHighlightRehype(query: string) {
  const q = query.trim();
  const lq = q.toLowerCase();

  const split = (text: string): HNode[] => {
    const lower = text.toLowerCase();
    const parts: HNode[] = [];
    let i = 0;
    for (;;) {
      const idx = lower.indexOf(lq, i);
      if (idx < 0) {
        if (i < text.length) parts.push({ type: "text", value: text.slice(i) });
        break;
      }
      if (idx > i) parts.push({ type: "text", value: text.slice(i, idx) });
      parts.push({
        type: "element",
        tagName: "mark",
        properties: { className: ["search-hl"] },
        children: [{ type: "text", value: text.slice(idx, idx + q.length) }],
      });
      i = idx + q.length;
    }
    return parts.length ? parts : [{ type: "text", value: text }];
  };

  const walk = (node: HNode): void => {
    if (!node.children) return;
    const next: HNode[] = [];
    for (const child of node.children) {
      if (child.type === "text" && typeof child.value === "string") {
        next.push(...split(child.value));
      } else {
        const skip =
          child.type === "element" &&
          child.tagName != null &&
          SKIP_TAGS.has(child.tagName);
        if (!skip) walk(child);
        next.push(child);
      }
    }
    node.children = next;
  };

  return function rehypeHighlightTerm() {
    return (tree: HNode): void => {
      if (!q) return;
      walk(tree);
    };
  };
}
