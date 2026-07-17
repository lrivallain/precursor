import { useEffect, useState } from "react";
import { Brain, Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Memory } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import { RefineTextarea } from "./RefineTextarea";

const KIND_SUGGESTIONS = ["context", "preference", "fact", "note"];

export function MemoriesTab() {
  const confirmAction = useConfirm();
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Memory | "new" | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    try {
      setMemories(await api.memories.list());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleDelete(memory: Memory): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete this ${memory.kind} memory?`,
        confirmLabel: "Delete memory",
        variant: "danger",
      }))
    )
      return;
    setBusyId(memory.id);
    setError(null);
    try {
      await api.memories.remove(memory.id);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-muted">
          Memories are short notes (context, preferences, facts) injected into
          every chat as standing context for the assistant.
        </p>
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs whitespace-nowrap"
        >
          <Plus size={12} /> New
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-500 border border-red-500/30 rounded p-2">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : memories.length === 0 ? (
        <div className="border border-dashed border-border rounded p-4 text-xs text-muted text-center space-y-1">
          <Brain size={18} className="mx-auto text-muted" />
          <div className="text-sm text-text">No memories yet</div>
          <p>Add one to give the assistant persistent context.</p>
        </div>
      ) : (
        <ul className="space-y-1.5">
          {memories.map((m) => (
            <li
              key={m.id}
              className="border border-border rounded px-2 py-1.5 flex items-start gap-2"
            >
              <span
                className="text-[10px] uppercase tracking-wide text-muted border border-border rounded px-1.5 py-px mt-0.5 shrink-0"
                title={m.kind}
              >
                {m.kind}
              </span>
              <div className="flex-1 min-w-0 text-sm whitespace-pre-wrap break-words">
                {m.content}
              </div>
              <button
                type="button"
                onClick={() => setEditing(m)}
                className="p-1 rounded hover:bg-surface text-muted hover:text-text"
                aria-label="Edit"
                data-tooltip="Edit"
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                onClick={() => void handleDelete(m)}
                disabled={busyId === m.id}
                className="p-1 rounded hover:bg-surface text-muted hover:text-red-500 disabled:opacity-40"
                aria-label="Delete"
                data-tooltip="Delete"
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <MemoryEditor
          memory={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            await load();
            setEditing(null);
          }}
        />
      )}
    </section>
  );
}

interface EditorProps {
  memory: Memory | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}

function MemoryEditor({ memory, onClose, onSaved }: EditorProps) {
  const [kind, setKind] = useState(memory?.kind ?? "context");
  const [content, setContent] = useState(memory?.content ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      const payload = { kind: kind.trim().toLowerCase(), content: content.trim() };
      if (memory) {
        await api.memories.update(memory.id, payload);
      } else {
        await api.memories.create(payload);
      }
      await onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[min(520px,100%)] max-h-[90vh] bg-bg border border-border rounded shadow-lg flex flex-col">
        <header className="flex items-center justify-between px-4 h-10 border-b border-border">
          <h3 className="font-semibold text-sm">
            {memory ? "Edit memory" : "New memory"}
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {error && (
            <div className="text-xs text-red-500 border border-red-500/30 rounded p-2">
              {error}
            </div>
          )}

          <div>
            <label className="block text-xs text-muted mb-1">Kind</label>
            <input
              type="text"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              list="memory-kind-suggestions"
              placeholder="context"
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono outline-none focus:border-accent"
            />
            <datalist id="memory-kind-suggestions">
              {KIND_SUGGESTIONS.map((k) => (
                <option key={k} value={k} />
              ))}
            </datalist>
            <p className="text-[10px] text-muted mt-1">
              Short tag shown in the chat system prompt (e.g. context,
              preference, fact). Lowercase letters, digits, hyphens.
            </p>
          </div>

          <div>
            <label className="block text-xs text-muted mb-1">Content</label>
            <RefineTextarea
              value={content}
              onValueChange={setContent}
              refineKind="memory"
              rows={6}
              placeholder="User is a Cloud Solution Architect at Microsoft."
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
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
            disabled={saving || !kind.trim() || !content.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            <Check size={14} />
            {saving ? "Saving…" : memory ? "Save" : "Create"}
          </button>
        </footer>
      </div>
    </div>
  );
}
