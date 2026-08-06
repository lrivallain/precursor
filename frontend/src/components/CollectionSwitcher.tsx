import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Layers, Plus, Settings2 } from "lucide-react";
import type { Collection } from "../lib/types";
import { collectionColor } from "../lib/collections";

interface Props {
  collections: Collection[];
  activeId: number | null;
  /** Unread message count per collection id; badges collections with activity. */
  unreadByCollection?: Record<number, number>;
  onSelect: (id: number) => void;
  /** Create a collection inline from the switcher (prompts for a name). */
  onCreate: (name: string) => void | Promise<void>;
  /** Open Settings → Collections for full management. */
  onManage: () => void;
}

/**
 * Collection switcher shown above the topic tree. Selecting a collection
 * filters the tree (and the pinned list) to that collection's topics.
 */
export function CollectionSwitcher({
  collections,
  activeId,
  unreadByCollection,
  onSelect,
  onCreate,
  onManage,
}: Props) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setCreating(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        setCreating(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (creating) inputRef.current?.focus();
  }, [creating]);

  const active = collections.find((c) => c.id === activeId) ?? null;
  const activeColor = collectionColor(active?.accent);
  // Unread sitting in collections you're not looking at — the only signal that
  // there is activity outside the filtered tree.
  const elsewhereUnread = collections.reduce(
    (n, c) => (c.id === activeId ? n : n + (unreadByCollection?.[c.id] ?? 0)),
    0,
  );

  async function submitDraft(): Promise<void> {
    const name = draft.trim();
    if (!name) return;
    setDraft("");
    setCreating(false);
    setOpen(false);
    await onCreate(name);
  }

  // A lone default collection adds no value as a picker — stay out of the way
  // until the user actually has something to switch between.
  if (collections.length <= 1 && !active?.github_repo) return null;

  return (
    <div ref={rootRef} className="relative px-3 py-2 border-b border-border">
      <button
        type="button"
        className="w-full flex items-center gap-2 px-2 py-1.5 text-sm rounded border border-border bg-surface hover:border-accent outline-none focus:border-accent"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Collection: ${active?.name ?? "none"} — switch collection`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`w-2 h-2 rounded-full shrink-0 ${activeColor.dot}`} aria-hidden />
        <span className="truncate flex-1 text-left">{active?.name ?? "Collection"}</span>
        {elsewhereUnread > 0 && (
          <span
            className="w-1.5 h-1.5 rounded-full bg-blue-500 shrink-0"
            aria-label={`${elsewhereUnread} unread in other collections`}
            data-tooltip="Unread in other collections"
          />
        )}
        <span className="text-xs text-muted shrink-0" title="Topics in this collection">
          {active?.topic_count ?? 0}
        </span>
        <ChevronDown size={13} className="text-muted shrink-0" />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute left-3 right-3 top-full z-30 mt-1 rounded border border-border bg-bg shadow-lg py-1 max-h-80 overflow-y-auto"
        >
          {collections.map((c) => {
            const color = collectionColor(c.accent);
            const unread = unreadByCollection?.[c.id] ?? 0;
            return (
              <button
                key={c.id}
                type="button"
                role="option"
                aria-selected={c.id === activeId}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm text-left hover:bg-surface"
                onClick={() => {
                  onSelect(c.id);
                  setOpen(false);
                }}
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${color.dot}`} aria-hidden />
                <span className={`truncate flex-1 ${unread > 0 ? "font-semibold" : ""}`}>
                  {c.name}
                </span>
                {unread > 0 && (
                  <span
                    className="inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[10px] font-medium rounded-full bg-blue-500 text-white"
                    aria-label={`${unread} unread ${unread === 1 ? "message" : "messages"}`}
                  >
                    {unread > 9 ? "9+" : unread}
                  </span>
                )}
                <span className="text-xs text-muted">{c.topic_count}</span>
                {c.id === activeId && <Check size={13} className="text-muted shrink-0" />}
              </button>
            );
          })}

          <div className="my-1 border-t border-border" />

          {creating ? (
            <div className="px-2.5 py-1.5">
              <input
                ref={inputRef}
                type="text"
                value={draft}
                placeholder="Collection name"
                className="w-full px-2 py-1 text-sm bg-surface border border-border rounded outline-none focus:border-accent"
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void submitDraft();
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    setCreating(false);
                    setDraft("");
                  }
                }}
                onBlur={() => void submitDraft()}
              />
            </div>
          ) : (
            <button
              type="button"
              className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm text-left text-muted hover:bg-surface hover:text-fg"
              onClick={() => setCreating(true)}
            >
              <Plus size={13} />
              New collection…
            </button>
          )}

          <button
            type="button"
            className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm text-left text-muted hover:bg-surface hover:text-fg"
            onClick={() => {
              setOpen(false);
              onManage();
            }}
          >
            <Settings2 size={13} />
            Manage collections
          </button>
        </div>
      )}
    </div>
  );
}

/** Compact collection badge used in menus and settings rows. */
export function CollectionChip({ collection }: { collection: Collection }) {
  const color = collectionColor(collection.accent);
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] ${color.chip}`}
    >
      <Layers size={10} />
      {collection.name}
    </span>
  );
}
