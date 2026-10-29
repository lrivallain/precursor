import { useCallback, useEffect, useState } from "react";
import { MessageSquare, MessageSquarePlus, MessagesSquare, Repeat2, X } from "lucide-react";
import { api } from "../lib/api";
import type { WsDiscussionRef } from "../lib/routes";
import type { Chat, Topic, Workspace, WorkspaceDiscussion } from "../lib/types";
import { useResizableWidth } from "../lib/useResizableWidth";
import { ChatPanel } from "./ChatPanel";
import { ChatSessionPanel } from "./ChatSessionPanel";
import { ResizeHandle } from "./ResizeHandle";

export function DiscussButton({ active, onClick }: { active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 text-xs ${
        active
          ? "border-accent/60 bg-accent/10 text-accent"
          : "border-border text-muted hover:bg-surface hover:text-text"
      }`}
      data-tooltip={
        active
          ? "Hide the discussion"
          : "Discuss this workspace with a model\nStart a chat or continue an existing one"
      }
    >
      <MessageSquare size={13} />
      Discuss
    </button>
  );
}

type Resolved =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "chat"; chat: Chat }
  | { status: "topic"; topic: Topic };

// The chat or topic named by the URL, re-read whenever the panel says it changed.
function useResolvedDiscussion(discussion: WsDiscussionRef) {
  const [resolved, setResolved] = useState<Resolved>({ status: "loading" });
  const { kind, ref } = discussion;

  useEffect(() => {
    let cancelled = false;
    setResolved({ status: "loading" });
    const load =
      kind === "chat"
        ? api.chats.getBySlug(ref).then((chat): Resolved => ({ status: "chat", chat }))
        : api.topics.getByPublicId(ref).then((topic): Resolved => ({ status: "topic", topic }));
    load
      .then((next) => !cancelled && setResolved(next))
      .catch((err) => !cancelled && setResolved({ status: "error", message: (err as Error).message }));
    return () => {
      cancelled = true;
    };
  }, [kind, ref]);

  const refresh = useCallback(async () => {
    try {
      if (kind === "chat") {
        const chat = await api.chats.getBySlug(ref);
        setResolved({ status: "chat", chat });
      } else {
        const topic = await api.topics.getByPublicId(ref);
        setResolved({ status: "topic", topic });
      }
    } catch {
      // the conversation may be gone; the next open surfaces the error
    }
  }, [kind, ref]);

  return { resolved, refresh };
}

interface WorkspaceDiscussionPanelProps {
  workspace: Workspace;
  activePath: string | null;
  /** The discussion the URL names; null while the picker is showing. */
  discussion: WsDiscussionRef | null;
  onSelect: (discussion: WsDiscussionRef) => void;
  onClose: () => void;
}

/**
 * The workspace's side panel: a real chat or topic, rendered with the same
 * components as the Chats and Topics sections. Hidden until the user opts in,
 * and the open file rides along on every turn so the model can see it.
 */
export function WorkspaceDiscussionPanel({
  workspace,
  activePath,
  discussion,
  onSelect,
  onClose,
}: WorkspaceDiscussionPanelProps) {
  const [picking, setPicking] = useState(discussion === null);
  const { width, onMouseDown: onResize } = useResizableWidth({
    storageKey: "precursor:workspace:discussionWidth",
    defaultWidth: 416,
    min: 320,
    max: 800,
    side: "left",
  });
  const select = (next: WsDiscussionRef) => {
    setPicking(false);
    onSelect(next);
  };
  const showPicker = picking || discussion === null;

  return (
    <aside
      // The inline width only applies from lg up: below that the panel is a
      // full-width overlay and the drag handle is hidden.
      style={{ "--discussion-w": `${width}px` } as React.CSSProperties}
      className="flex min-h-0 min-w-0 w-full shrink flex-col border-l border-border bg-bg lg:relative lg:min-w-70 lg:w-[var(--discussion-w)] lg:max-w-[65%] max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-20"
      aria-label="Discussion"
      data-testid="workspace-discussion"
    >
      <div className="max-lg:hidden">
        <ResizeHandle onMouseDown={onResize} side="left" />
      </div>
      {showPicker ? (
        <DiscussionPicker
          workspace={workspace}
          activePath={activePath}
          onSelect={select}
          onClose={onClose}
          onCancel={discussion ? () => setPicking(false) : undefined}
        />
      ) : (
        <OpenDiscussion
          discussion={discussion}
          workspace={workspace}
          activePath={activePath}
          onSwitch={() => setPicking(true)}
          onClose={onClose}
        />
      )}
    </aside>
  );
}

function PanelHeader({
  title,
  icon,
  onSwitch,
  onClose,
}: {
  title: string;
  icon: React.ReactNode;
  onSwitch?: () => void;
  onClose: () => void;
}) {
  return (
    <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border px-3 text-sm">
      <span className="shrink-0 text-muted">{icon}</span>
      <span className="min-w-0 flex-1 truncate font-medium" title={title}>
        {title}
      </span>
      {onSwitch && (
        <button
          type="button"
          onClick={onSwitch}
          className="rounded p-1 text-muted hover:bg-surface hover:text-text"
          data-tooltip="Switch discussion"
          aria-label="Switch discussion"
        >
          <Repeat2 size={14} />
        </button>
      )}
      <button
        type="button"
        onClick={onClose}
        className="rounded p-1 text-muted hover:bg-surface hover:text-text"
        data-tooltip="Close the discussion"
        aria-label="Close the discussion"
      >
        <X size={14} />
      </button>
    </div>
  );
}

function OpenDiscussion({
  discussion,
  workspace,
  activePath,
  onSwitch,
  onClose,
}: {
  discussion: WsDiscussionRef;
  workspace: Workspace;
  activePath: string | null;
  onSwitch: () => void;
  onClose: () => void;
}) {
  const { resolved, refresh } = useResolvedDiscussion(discussion);
  const workspaceContext = { workspace_id: workspace.id, path: activePath };

  // The discussion follows the file being viewed: attach it on every switch.
  const kind = resolved.status === "chat" || resolved.status === "topic" ? resolved.status : null;
  const containerId =
    resolved.status === "chat" ? resolved.chat.id : resolved.status === "topic" ? resolved.topic.id : null;
  useEffect(() => {
    if (!kind || containerId === null || !activePath) return;
    void api.artifacts
      .attach(kind, containerId, {
        workspace_id: workspace.id,
        path: activePath,
        source: "opened",
      })
      .catch(() => {
        // an unattachable file (gone, outside the tree) just stays unattached
      });
  }, [kind, containerId, workspace.id, activePath]);

  if (resolved.status === "loading") {
    return (
      <>
        <PanelHeader title="Discussion" icon={<MessageSquare size={14} />} onClose={onClose} />
        <p className="p-4 text-sm text-muted">Loading…</p>
      </>
    );
  }
  if (resolved.status === "error") {
    return (
      <>
        <PanelHeader title="Discussion" icon={<MessageSquare size={14} />} onSwitch={onSwitch} onClose={onClose} />
        <p className="p-4 text-sm text-red-500">
          Couldn&apos;t open this discussion: {resolved.message}
        </p>
      </>
    );
  }

  if (resolved.status === "chat") {
    const { chat } = resolved;
    return (
      <>
        <PanelHeader title={chat.title} icon={<MessageSquare size={14} />} onSwitch={onSwitch} onClose={onClose} />
        <div className="min-h-0 flex-1">
          <ChatSessionPanel
            key={chat.id}
            chat={chat}
            workspaceContext={workspaceContext}
            onChatUpdated={() => void refresh()}
            onArchived={onClose}
            onSetRole={async (role) => {
              await api.chats.update(chat.id, { role });
              await refresh();
            }}
          />
        </div>
      </>
    );
  }

  const { topic } = resolved;
  return (
    <>
      <PanelHeader title={topic.title} icon={<MessagesSquare size={14} />} onSwitch={onSwitch} onClose={onClose} />
      <div className="min-h-0 flex-1">
        <ChatPanel
          key={topic.id}
          topic={topic}
          workspaceContext={workspaceContext}
          onTopicUpdated={() => void refresh()}
          onArchived={onClose}
          onSetRole={async (role) => {
            await api.topics.update(topic.id, { role });
            await refresh();
          }}
        />
      </div>
    </>
  );
}

function whenLabel(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString();
}

function DiscussionPicker({
  workspace,
  activePath,
  onSelect,
  onClose,
  onCancel,
}: {
  workspace: Workspace;
  activePath: string | null;
  onSelect: (discussion: WsDiscussionRef) => void;
  onClose: () => void;
  /** Back to the open discussion, when there is one. */
  onCancel?: () => void;
}) {
  const [continuing, setContinuing] = useState<WorkspaceDiscussion[] | null>(null);
  const [query, setQuery] = useState("");
  const [chats, setChats] = useState<Chat[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.artifacts
      .discussions(workspace.id, activePath)
      .then((list) => !cancelled && setContinuing(list))
      .catch((err) => !cancelled && setError((err as Error).message));
    return () => {
      cancelled = true;
    };
  }, [workspace.id, activePath]);

  // Any chat or topic can be brought in, not only the ones already attached.
  useEffect(() => {
    const q = query.trim();
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void Promise.all([api.chats.list(q || undefined), api.topics.list(q || undefined)])
        .then(([c, t]) => {
          if (cancelled) return;
          setChats(c.slice(0, 8));
          setTopics(t.slice(0, 8));
        })
        .catch(() => undefined);
    }, q ? 250 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  async function pick(kind: "chat" | "topic", id: number, ref: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      if (activePath) {
        await api.artifacts.attach(kind, id, {
          workspace_id: workspace.id,
          path: activePath,
          source: "opened",
        });
      }
      onSelect({ kind, ref });
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function startChat(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const chat = await api.chats.create({ title: "New chat", autoname: true });
      await pick("chat", chat.id, chat.slug);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const attached = new Set((continuing ?? []).map((d) => `${d.kind}:${d.id}`));
  const others: { kind: "chat" | "topic"; id: number; ref: string; title: string }[] = [
    ...chats.map((c) => ({ kind: "chat" as const, id: c.id, ref: c.slug, title: c.title })),
    ...topics.map((t) => ({ kind: "topic" as const, id: t.id, ref: t.public_id, title: t.title })),
  ].filter((item) => !attached.has(`${item.kind}:${item.id}`));

  return (
    <>
      <PanelHeader
        title="Discuss"
        icon={<MessageSquare size={14} />}
        onSwitch={onCancel}
        onClose={onClose}
      />
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3 text-sm">
        <button
          type="button"
          onClick={() => void startChat()}
          disabled={busy}
          className="flex w-full items-center gap-2 rounded-md border border-accent/50 bg-accent/10 px-3 py-2 text-left text-accent hover:bg-accent/15 disabled:opacity-50"
        >
          <MessageSquarePlus size={15} />
          <span className="min-w-0 flex-1">
            <span className="block font-medium">New chat</span>
            <span className="block truncate text-xs text-muted">
              {activePath ? `About ${activePath}` : `About ${workspace.name}`}
            </span>
          </span>
        </button>
        {error && <p className="text-xs text-red-500">{error}</p>}

        <section>
          <h3 className="mb-1 text-xs uppercase tracking-wide text-muted">Continue a discussion</h3>
          {continuing === null ? (
            <p className="text-xs text-muted">Loading…</p>
          ) : continuing.length === 0 ? (
            <p className="text-xs text-muted">
              Nothing attaches files from this workspace yet. Chats and topics land here once the
              assistant writes a file, or when you attach one.
            </p>
          ) : (
            <ul className="space-y-1">
              {continuing.map((d) => (
                <li key={`${d.kind}:${d.id}`}>
                  <DiscussionRow
                    kind={d.kind}
                    title={d.title}
                    detail={
                      d.has_file && activePath
                        ? "On this file"
                        : `${d.paths.length} file${d.paths.length === 1 ? "" : "s"} · ${whenLabel(d.updated_at)}`
                    }
                    highlight={d.has_file && !!activePath}
                    disabled={busy}
                    onClick={() => void pick(d.kind, d.id, d.kind === "chat" ? d.slug : (d.public_id ?? ""))}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <h3 className="mb-1 text-xs uppercase tracking-wide text-muted">Open another</h3>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats and topics…"
            className="mb-1.5 w-full rounded border border-border bg-bg px-2 py-1 text-xs"
            aria-label="Search chats and topics"
          />
          <ul className="space-y-1">
            {others.map((o) => (
              <li key={`${o.kind}:${o.id}`}>
                <DiscussionRow
                  kind={o.kind}
                  title={o.title}
                  detail={o.kind === "chat" ? "Chat" : "Topic"}
                  disabled={busy}
                  onClick={() => void pick(o.kind, o.id, o.ref)}
                />
              </li>
            ))}
            {others.length === 0 && <li className="text-xs text-muted">No matches.</li>}
          </ul>
        </section>
      </div>
    </>
  );
}

function DiscussionRow({
  kind,
  title,
  detail,
  highlight,
  disabled,
  onClick,
}: {
  kind: "chat" | "topic";
  title: string;
  detail: string;
  highlight?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left hover:bg-surface disabled:opacity-50 ${
        highlight ? "border-accent/50 bg-accent/5" : "border-border bg-surface/60"
      }`}
    >
      {kind === "chat" ? (
        <MessageSquare size={13} className="shrink-0 text-muted" aria-label="Chat" />
      ) : (
        <MessagesSquare size={13} className="shrink-0 text-muted" aria-label="Topic" />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate">{title}</span>
        <span className={`block truncate text-[11px] ${highlight ? "text-accent" : "text-muted"}`}>
          {detail}
        </span>
      </span>
    </button>
  );
}
