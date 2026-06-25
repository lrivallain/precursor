import { useState } from "react";
import { Archive, ArrowUpRight, Eraser, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Chat, Topic } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

interface Props {
  chat: Chat;
  onClose: () => void;
  onSaved: (chat: Chat) => void;
  onDeleted: () => void;
  onArchived: () => void;
  onCleared: () => void;
  onPromoted: (topic: Topic) => void;
}

// Mirrors TopicSettingsPanel's structure (drawer + Title/Slug/Description +
// footer Save) so chats and topics feel like the same surface, minus the
// topic-only bits (parent tree, GitHub issue, schedule) and plus the
// chat-only "Promote to topic" transform.
export function ChatSettingsPanel({
  chat,
  onClose,
  onSaved,
  onDeleted,
  onArchived,
  onCleared,
  onPromoted,
}: Props) {
  const confirmAction = useConfirm();
  const [title, setTitle] = useState(chat.title);
  const [slug, setSlug] = useState(chat.slug);
  const [description, setDescription] = useState(chat.description ?? "");
  const [asSystemPrompt, setAsSystemPrompt] = useState(chat.description_as_system_prompt);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(): Promise<void> {
    const trimmed = title.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateChat(chat.id, {
        title: trimmed,
        slug: slug.trim() && slug.trim() !== chat.slug ? slug.trim() : undefined,
        description: description.trim() ? description.trim() : null,
        description_as_system_prompt: asSystemPrompt,
      });
      onSaved(updated);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function promote(): Promise<void> {
    if (
      !(await confirmAction({
        message:
          "Promote this chat to a full topic? It moves out of Chats and gains the topic tree + GitHub features. The transcript is kept.",
        confirmLabel: "Promote chat",
        variant: "warning",
      }))
    )
      return;
    setPromoting(true);
    setError(null);
    try {
      onPromoted(await api.promoteChat(chat.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPromoting(false);
    }
  }

  async function clearChat(): Promise<void> {
    if (
      !(await confirmAction({
        message: "Erase the entire transcript for this chat?",
        confirmLabel: "Erase transcript",
        variant: "danger",
      }))
    )
      return;
    setClearing(true);
    setError(null);
    try {
      await api.clearChatMessages(chat.id);
      onCleared();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setClearing(false);
    }
  }

  async function archive(): Promise<void> {
    setArchiving(true);
    setError(null);
    try {
      await api.archiveChat(chat.id);
      onArchived();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setArchiving(false);
    }
  }

  async function remove(): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete "${chat.title}" and all its messages?`,
        confirmLabel: "Delete chat",
        variant: "danger",
      }))
    )
      return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteChat(chat.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[min(480px,100%)] h-full bg-bg border-l border-border flex flex-col">
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold truncate">Chat settings</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="p-4 space-y-4">
            <section>
              <label className="block text-xs text-muted mb-1">Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
              />
            </section>

            <section>
              <label className="block text-xs text-muted mb-1">Slug</label>
              <input
                type="text"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent font-mono"
              />
              <p className="text-[11px] text-muted mt-1">
                URL for this chat — share or bookmark{" "}
                <code className="font-mono">/chats/{slug || chat.slug}</code> to deep-link here.
                The server normalizes the value and appends <code>-2</code>, <code>-3</code>… on
                collision.
              </p>
            </section>

            <section>
              <label className="block text-xs text-muted mb-1">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                placeholder="Optional — steers the assistant's system context."
                className="w-full resize-y bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
              />
              <label className="flex items-start gap-2 mt-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={asSystemPrompt}
                  disabled={!description.trim()}
                  onChange={(e) => setAsSystemPrompt(e.target.checked)}
                  className="mt-0.5 disabled:opacity-50"
                />
                <span className="text-xs text-muted">
                  Use as system prompt
                  <span className="block text-[11px]">
                    Enforce the description on every turn instead of adding it once as context.
                  </span>
                </span>
              </label>
            </section>

            {error && <p className="text-xs text-red-500">{error}</p>}

            <section className="pt-2 border-t border-border space-y-3">
              <div>
                <button
                  onClick={() => void promote()}
                  disabled={promoting}
                  className="flex items-center gap-2 text-sm text-accent hover:opacity-80 disabled:opacity-50"
                >
                  <ArrowUpRight size={14} />
                  {promoting ? "Promoting…" : "Promote to topic"}
                </button>
                <p className="text-[11px] text-muted mt-1">
                  Moves this conversation into Topics, where it gains the tree organisation and
                  GitHub issue association. The transcript is kept.
                </p>
              </div>
              <div>
                <button
                  onClick={() => void clearChat()}
                  disabled={clearing}
                  className="flex items-center gap-2 text-sm text-amber-500 hover:text-amber-400 disabled:opacity-50"
                >
                  <Eraser size={14} />
                  {clearing ? "Clearing…" : "Clear chat"}
                </button>
                <p className="text-[11px] text-muted mt-1">
                  Erases every message in this chat. The chat itself is kept.
                </p>
              </div>
              <div>
                <button
                  onClick={() => void archive()}
                  disabled={archiving}
                  className="flex items-center gap-2 text-sm text-muted hover:text-text disabled:opacity-50"
                >
                  <Archive size={14} />
                  {archiving ? "Archiving…" : "Archive chat"}
                </button>
                <p className="text-[11px] text-muted mt-1">
                  Hides the chat from the sidebar but keeps its messages. Restore it any time from
                  the archive (click your profile in the sidebar).
                </p>
              </div>
              <div>
                <button
                  onClick={() => void remove()}
                  disabled={deleting}
                  className="flex items-center gap-2 text-sm text-red-500 hover:text-red-400 disabled:opacity-50"
                >
                  <Trash2 size={14} />
                  {deleting ? "Deleting…" : "Delete chat"}
                </button>
                <p className="text-[11px] text-muted mt-1">
                  Removes the chat and all its messages. This can't be undone.
                </p>
              </div>
            </section>
          </div>
        </div>

        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={!title.trim() || saving}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}
