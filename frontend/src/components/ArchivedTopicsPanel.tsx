import { useEffect, useState } from "react";
import { Archive, ExternalLink, RotateCcw, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Topic } from "../lib/types";

interface Props {
  onClose: () => void;
  onRestored: (topic: Topic) => void;
  onDeleted: (topicId: number) => void;
}

export function ArchivedTopicsPanel({ onClose, onRestored, onDeleted }: Props) {
  const [topics, setTopics] = useState<Topic[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function load(): Promise<void> {
    try {
      const list = await api.listArchivedTopics();
      setTopics(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setTopics([]);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function restore(t: Topic): Promise<void> {
    setBusyId(t.id);
    setError(null);
    try {
      const updated = await api.unarchiveTopic(t.id);
      setTopics((prev) => prev?.filter((x) => x.id !== t.id) ?? []);
      onRestored(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(t: Topic): Promise<void> {
    if (
      !window.confirm(
        `Permanently delete "${t.title}" and all its messages? This cannot be undone.`,
      )
    )
      return;
    setBusyId(t.id);
    setError(null);
    try {
      await api.deleteTopic(t.id);
      setTopics((prev) => prev?.filter((x) => x.id !== t.id) ?? []);
      onDeleted(t.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[min(520px,100%)] h-full bg-bg border-l border-border flex flex-col">
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold truncate flex items-center gap-2">
            <Archive size={16} className="text-muted" />
            Archive
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={18} />
          </button>
        </header>

        <div className="px-4 py-3 border-b border-border text-[12px] text-muted">
          Archived topics are hidden from the sidebar but keep their messages,
          GitHub link, and parent. Restore one to bring it back where it was.
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {error && (
            <p className="text-xs text-red-500 mb-2 px-1">{error}</p>
          )}
          {topics === null ? (
            <p className="text-xs text-muted px-1">Loading…</p>
          ) : topics.length === 0 ? (
            <p className="text-xs text-muted px-1">No archived topics.</p>
          ) : (
            <ul className="space-y-2">
              {topics.map((t) => {
                const archivedAt = t.archived_at
                  ? new Date(t.archived_at)
                  : null;
                return (
                  <li
                    key={t.id}
                    className="flex items-start gap-3 rounded border border-border bg-surface px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">
                        {t.title}
                      </div>
                      <div className="text-[11px] text-muted truncate">
                        #{t.slug}
                        {archivedAt && (
                          <>
                            {" · archived "}
                            {archivedAt.toLocaleString()}
                          </>
                        )}
                      </div>
                      {t.github_repo && t.github_issue_number !== null && (
                        <a
                          href={`https://github.com/${t.github_repo}/issues/${t.github_issue_number}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 mt-1 text-[11px] text-muted hover:text-text"
                        >
                          <ExternalLink size={10} />
                          {t.github_repo}#{t.github_issue_number}
                        </a>
                      )}
                    </div>
                    <div className="flex flex-col gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={() => void restore(t)}
                        disabled={busyId === t.id}
                        className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
                      >
                        <RotateCcw size={12} />
                        Restore
                      </button>
                      <button
                        type="button"
                        onClick={() => void remove(t)}
                        disabled={busyId === t.id}
                        className="flex items-center gap-1 px-2 py-1 rounded border border-border text-red-500 text-xs hover:bg-bg disabled:opacity-50"
                      >
                        <Trash2 size={12} />
                        Delete
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
