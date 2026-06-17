import { useState } from "react";
import { ArrowUpRight, Eraser, Loader2, Pin, PinOff, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Chat, Topic } from "../lib/types";

interface Props {
  chat: Chat;
  onClose: () => void;
  onSaved: (chat: Chat) => void;
  onDeleted: () => void;
  onCleared: () => void;
  onPromoted: (topic: Topic) => void;
}

export function ChatSettingsPanel({
  chat,
  onClose,
  onSaved,
  onDeleted,
  onCleared,
  onPromoted,
}: Props) {
  const [title, setTitle] = useState(chat.title);
  const [description, setDescription] = useState(chat.description ?? "");
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    title.trim() !== chat.title || (description.trim() || null) !== (chat.description ?? null);

  async function save(): Promise<void> {
    const next = title.trim();
    if (!next) {
      setError("Title can't be empty.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateChat(chat.id, {
        title: next,
        description: description.trim() || null,
      });
      onSaved(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function togglePin(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.updateChat(chat.id, { pinned: !chat.pinned }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function clear(): Promise<void> {
    if (!window.confirm("Erase the entire transcript for this chat?")) return;
    setClearing(true);
    setError(null);
    try {
      await api.clearChatMessages(chat.id);
      onCleared();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setClearing(false);
    }
  }

  async function promote(): Promise<void> {
    if (
      !window.confirm(
        "Promote this chat to a full topic? It moves out of Chats and gains the " +
          "topic tree + GitHub features. The transcript is kept.",
      )
    )
      return;
    setPromoting(true);
    setError(null);
    try {
      const topic = await api.promoteChat(chat.id);
      onPromoted(topic);
    } catch (e) {
      setError((e as Error).message);
      setPromoting(false);
    }
  }

  async function remove(): Promise<void> {
    if (!window.confirm("Delete this chat and its transcript? This can't be undone.")) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteChat(chat.id);
      onDeleted();
    } catch (e) {
      setError((e as Error).message);
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

        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {error && (
            <div className="rounded border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-400">
              {error}
            </div>
          )}

          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted">Title</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted">Description</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Optional — steers the assistant's system context."
              className="w-full resize-y rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </label>

          <div className="flex items-center gap-2">
            <button
              onClick={() => void save()}
              disabled={!dirty || saving}
              className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              {saving && <Loader2 size={14} className="animate-spin" />} Save changes
            </button>
            <button
              onClick={() => void togglePin()}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface disabled:opacity-50"
            >
              {chat.pinned ? <PinOff size={14} /> : <Pin size={14} />}
              {chat.pinned ? "Unpin" : "Pin"}
            </button>
          </div>

          <div className="border-t border-border pt-4 space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              Transform
            </h3>
            <button
              onClick={() => void promote()}
              disabled={promoting}
              className="inline-flex items-center gap-1.5 rounded border border-accent/40 bg-bg px-3 py-1.5 text-sm text-accent hover:bg-accent/10 disabled:opacity-50"
            >
              {promoting ? <Loader2 size={14} className="animate-spin" /> : <ArrowUpRight size={14} />}
              Promote to topic
            </button>
            <p className="text-xs text-muted">
              Moves this conversation into Topics, where it gains the tree organisation and
              GitHub issue association.
            </p>
          </div>

          <div className="border-t border-border pt-4 space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
              Danger zone
            </h3>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => void clear()}
                disabled={clearing}
                className="inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface disabled:opacity-50"
              >
                {clearing ? <Loader2 size={14} className="animate-spin" /> : <Eraser size={14} />}
                Clear transcript
              </button>
              <button
                onClick={() => void remove()}
                disabled={deleting}
                className="inline-flex items-center gap-1.5 rounded border border-red-500/40 px-3 py-1.5 text-sm text-red-400 hover:bg-red-500/10 disabled:opacity-50"
              >
                {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                Delete chat
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
