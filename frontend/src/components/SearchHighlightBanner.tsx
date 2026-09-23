import { Search, X } from "lucide-react";
import type { SearchHighlightController } from "../lib/useSearchHighlightController";

// The strip under the shared header naming the active "find" term, with a way
// to drop it. Hidden at home, where there's no conversation to highlight.
export function SearchHighlightBanner({
  controller,
  atHome,
}: {
  controller: SearchHighlightController;
  atHome: boolean;
}) {
  const { searchHighlight, setSearchHighlight } = controller;
  if (!searchHighlight.trim() || atHome) return null;
  return (
    <div className="flex items-center justify-center gap-2 border-b border-border bg-accent/5 px-3 py-1 text-[11px] text-muted">
      <Search size={12} className="shrink-0" />
      <span>
        Highlighting{" "}
        <span className="font-medium text-text">“{searchHighlight.trim()}”</span>
      </span>
      <button
        type="button"
        onClick={() => setSearchHighlight("")}
        className="ml-1 inline-flex items-center gap-0.5 rounded px-1 py-0.5 hover:text-red-500"
        aria-label="Clear highlight"
        data-tooltip="Clear highlight"
      >
        <X size={12} />
        Clear
      </button>
    </div>
  );
}
