import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Chat, Collection, TopicNode } from "../lib/types";
import { Select } from "./Select";

interface Props {
  chat: Chat;
  /** Collection preselected in the picker (the one the user is looking at). */
  collectionId?: number | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (collectionId: number | null, parentId: number | null) => void;
}

function flattenTree(
  nodes: TopicNode[],
  depth = 0,
  out: { id: number; title: string; depth: number }[] = [],
) {
  for (const n of nodes) {
    out.push({ id: n.id, title: n.title, depth });
    flattenTree(n.children, depth + 1, out);
  }
  return out;
}

// Confirmation modal for "Promote to topic". A side chat's destination is fixed
// by the topic it came from, so only ordinary chats get the pickers.
export function PromoteChatDialog({ chat, collectionId = null, busy, onCancel, onConfirm }: Props) {
  const isSideChat = chat.parent_topic_id != null;
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collection, setCollection] = useState<number | "">(collectionId ?? "");
  const [parent, setParent] = useState<number | "">("");
  const [tree, setTree] = useState<TopicNode[]>([]);

  useEffect(() => {
    if (isSideChat) return;
    void api.collections
      .list()
      .then((list) => {
        setCollections(list);
        setCollection((cur) => cur || (list.find((c) => c.is_default)?.id ?? list[0]?.id ?? ""));
      })
      .catch(() => undefined);
  }, [isSideChat]);

  useEffect(() => {
    if (isSideChat || collection === "") return;
    let live = true;
    void api.topics
      .tree(collection)
      .then((t) => live && setTree(t))
      .catch(() => live && setTree([]));
    return () => {
      live = false;
    };
  }, [collection, isSideChat]);

  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape" && !busy) onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  return (
    <div
      className="fixed inset-0 z-[80] bg-black/50 flex items-center justify-center p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="promote-chat-title"
        className="w-[min(520px,100%)] rounded-lg border border-border bg-bg shadow-xl"
      >
        <div className="border-b border-border px-4 py-3">
          <h2 id="promote-chat-title" className="text-sm font-semibold">
            Promote chat to topic
          </h2>
        </div>
        <div className="px-4 py-3 space-y-3">
          <p className="text-sm text-text">
            {isSideChat
              ? `Promote this chat to a sub-topic of "${chat.parent_topic_title}"? It moves out of Chats and gains the topic tree + GitHub features. The transcript is kept.`
              : "Promote this chat to a topic? It moves out of Chats and gains the topic tree + GitHub features. The transcript is kept."}
          </p>
          {!isSideChat && collections.length > 0 && (
            <>
              <div>
                <label className="block text-xs text-muted mb-1">Target collection</label>
                <Select
                  value={collection === "" ? "" : String(collection)}
                  onChange={(v) => {
                    setCollection(v === "" ? "" : Number(v));
                    setParent("");
                  }}
                  ariaLabel="Target collection"
                  fullWidth
                  options={collections.map((c) => ({ value: String(c.id), label: c.name }))}
                />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Parent topic</label>
                <Select
                  value={parent === "" ? "" : String(parent)}
                  onChange={(v) => setParent(v === "" ? "" : Number(v))}
                  ariaLabel="Parent topic"
                  fullWidth
                  options={[
                    { value: "", label: "— top level —" },
                    ...flattenTree(tree).map((o) => ({
                      value: String(o.id),
                      label: `${"\u00A0".repeat(o.depth * 2)}${o.title}`,
                    })),
                  ]}
                />
              </div>
            </>
          )}
        </div>
        <div className="border-t border-border px-4 py-3 flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface disabled:opacity-50"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy}
            className="px-3 py-1.5 rounded text-sm bg-amber-500 hover:bg-amber-400 text-black disabled:opacity-50"
            onClick={() =>
              onConfirm(collection === "" ? collectionId : collection, parent === "" ? null : parent)
            }
          >
            {busy ? "Promoting…" : "Promote chat"}
          </button>
        </div>
      </div>
    </div>
  );
}
