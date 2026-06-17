import { useEffect, useState } from "react";
import {
  Archive,
  ExternalLink,
  MessageSquare,
  MessagesSquare,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import type { Chat, Topic } from "../lib/types";

interface Props {
  onClose: () => void;
  onTopicRestored: (topic: Topic) => void;
  onTopicDeleted: (topicId: number) => void;
  onChatRestored: (chat: Chat) => void;
  onChatDeleted: (chatId: number) => void;
}

type Tab = "topics" | "chats";

// Archive is shared across modes: topics and chats are both archivable, and
// each restores into its own section. The view lists both regardless of which
// mode the user is currently in.
export function ArchivePanel({
  onClose,
  onTopicRestored,
  onTopicDeleted,
  onChatRestored,
  onChatDeleted,
}: Props) {
  const [tab, setTab] = useState<Tab>("topics");
  const [topics, setTopics] = useState<Topic[] | null>(null);
  const [chats, setChats] = useState<Chat[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [t, c] = await Promise.all([
          api.listArchivedTopics(),
          api.listArchivedChats(),
        ]);
        setTopics(t);
        setChats(c);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setTopics([]);
        setChats([]);
      }
    })();
  }, []);

  async function restoreTopic(t: Topic): Promise<void> {
    setBusy(`t${t.id}`);
    setError(null);
    try {
      const updated = await api.unarchiveTopic(t.id);
      setTopics((prev) => prev?.filter((x) => x.id !== t.id) ?? []);
      onTopicRestored(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function deleteTopic(t: Topic): Promise<void> {
    if (!window.confirm(`Permanently delete "${t.title}" and all its messages?`)) return;
    setBusy(`t${t.id}`);
    setError(null);
    try {
      await api.deleteTopic(t.id);
      setTopics((prev) => prev?.filter((x) => x.id !== t.id) ?? []);
      onTopicDeleted(t.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function restoreChat(c: Chat): Promise<void> {
    setBusy(`c${c.id}`);
    setError(null);
    try {
      const updated = await api.unarchiveChat(c.id);
      setChats((prev) => prev?.filter((x) => x.id !== c.id) ?? []);
      onChatRestored(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function deleteChat(c: Chat): Promise<void> {
    if (!window.confirm(`Permanently delete "${c.title}" and all its messages?`)) return;
    setBusy(`c${c.id}`);
    setError(null);
    try {
      await api.deleteChat(c.id);
      setChats((prev) => prev?.filter((x) => x.id !== c.id) ?? []);
      onChatDeleted(c.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const topicCount = topics?.length ?? 0;
  const chatCount = chats?.length ?? 0;

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

        <nav className="flex border-b border-border text-sm">
          <button
            className={`flex items-center gap-1.5 px-4 py-2 ${
              tab === "topics"
                ? "border-b-2 border-accent text-accent"
                : "text-muted hover:text-text"
            }`}
            onClick={() => setTab("topics")}
          >
            <MessagesSquare size={14} /> Topics
            {topicCount > 0 && <span className="text-xs opacity-70">({topicCount})</span>}
          </button>
          <button
            className={`flex items-center gap-1.5 px-4 py-2 ${
              tab === "chats"
                ? "border-b-2 border-accent text-accent"
                : "text-muted hover:text-text"
            }`}
            onClick={() => setTab("chats")}
          >
            <MessageSquare size={14} /> Chats
            {chatCount > 0 && <span className="text-xs opacity-70">({chatCount})</span>}
          </button>
        </nav>

        <div className="flex-1 overflow-y-auto p-3">
          {error && <p className="text-xs text-red-500 mb-2 px-1">{error}</p>}

          {tab === "topics" ? (
            topics === null ? (
              <p className="text-xs text-muted px-1">Loading…</p>
            ) : topics.length === 0 ? (
              <p className="text-xs text-muted px-1">No archived topics.</p>
            ) : (
              <ul className="space-y-2">
                {topics.map((t) => (
                  <li
                    key={t.id}
                    className="flex items-start gap-3 rounded border border-border bg-surface px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{t.title}</div>
                      <div className="text-[11px] text-muted truncate">
                        #{t.slug}
                        {t.archived_at && <> · archived {new Date(t.archived_at).toLocaleString()}</>}
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
                    <RestoreDeleteButtons
                      busy={busy === `t${t.id}`}
                      onRestore={() => void restoreTopic(t)}
                      onDelete={() => void deleteTopic(t)}
                    />
                  </li>
                ))}
              </ul>
            )
          ) : chats === null ? (
            <p className="text-xs text-muted px-1">Loading…</p>
          ) : chats.length === 0 ? (
            <p className="text-xs text-muted px-1">No archived chats.</p>
          ) : (
            <ul className="space-y-2">
              {chats.map((c) => (
                <li
                  key={c.id}
                  className="flex items-start gap-3 rounded border border-border bg-surface px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium truncate">{c.title}</div>
                    <div className="text-[11px] text-muted truncate">
                      #{c.slug}
                      {c.archived_at && <> · archived {new Date(c.archived_at).toLocaleString()}</>}
                    </div>
                  </div>
                  <RestoreDeleteButtons
                    busy={busy === `c${c.id}`}
                    onRestore={() => void restoreChat(c)}
                    onDelete={() => void deleteChat(c)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function RestoreDeleteButtons({
  busy,
  onRestore,
  onDelete,
}: {
  busy: boolean;
  onRestore: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex flex-col gap-1 shrink-0">
      <button
        type="button"
        onClick={onRestore}
        disabled={busy}
        className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
      >
        <RotateCcw size={12} />
        Restore
      </button>
      <button
        type="button"
        onClick={onDelete}
        disabled={busy}
        className="flex items-center gap-1 px-2 py-1 rounded border border-border text-red-500 text-xs hover:bg-bg disabled:opacity-50"
      >
        <Trash2 size={12} />
        Delete
      </button>
    </div>
  );
}
