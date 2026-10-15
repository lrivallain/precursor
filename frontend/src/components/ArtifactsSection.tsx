import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, Paperclip, Plus, X } from "lucide-react";
import { api } from "../lib/api";
import { eventBus } from "../lib/events";
import type { WsDiscussionRef } from "../lib/routes";
import type {
  ConversationArtifact,
  ReminderContainer,
  Workspace,
  WorkspaceFileNode,
} from "../lib/types";
import { openWorkspaceFile } from "../lib/workspaceLink";

export interface ArtifactsControls {
  items: ConversationArtifact[];
  attach: (workspaceId: number, path: string) => Promise<void>;
  detach: (artifactId: number) => Promise<void>;
}

/**
 * The workspace files attached to a topic or chat, kept current from the event
 * bus: the assistant writing a file records it server-side, and that publishes
 * the container's change event.
 */
export function useConversationArtifacts(
  kind: ReminderContainer,
  id: number,
): ArtifactsControls {
  const [items, setItems] = useState<ConversationArtifact[]>([]);

  const load = useCallback(async () => {
    try {
      setItems(await api.artifacts.list(kind, id));
    } catch {
      // non-fatal: the section just keeps its last list
    }
  }, [kind, id]);

  useEffect(() => {
    setItems([]);
    void load();
  }, [load]);

  useEffect(() => {
    eventBus.start();
    let timer: number | undefined;
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void load(), 300);
    };
    const off = eventBus.subscribe((event) => {
      if (kind === "topic" && event.type === "topic.changed") {
        if (event.topic_id == null || event.topic_id === id) schedule();
      } else if (kind === "chat" && event.type === "chat.changed") {
        if (event.chat_id == null || event.chat_id === id) schedule();
      }
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, [kind, id, load]);

  const attach = useCallback(
    async (workspaceId: number, path: string) => {
      await api.artifacts.attach(kind, id, { workspace_id: workspaceId, path, source: "manual" });
      await load();
    },
    [kind, id, load],
  );

  const detach = useCallback(
    async (artifactId: number) => {
      await api.artifacts.detach(kind, id, artifactId);
      setItems((prev) => prev.filter((a) => a.id !== artifactId));
    },
    [kind, id],
  );

  return { items, attach, detach };
}

function baseName(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function dirName(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(0, -1).join("/");
}

/** Where an artifact came from, for its tooltip. */
const SOURCE_LABEL: Record<ConversationArtifact["source"], string> = {
  assistant: "Written by the assistant",
  manual: "Attached by you",
  opened: "Opened from the workspace",
};

export function ArtifactsSection({
  items,
  attach,
  detach,
  discussion,
}: ArtifactsControls & {
  /** What the workspace shows beside the file when an artifact is opened. */
  discussion: WsDiscussionRef;
}) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<void>): Promise<void> {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div data-testid="artifacts">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted">Artifacts</span>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-accent hover:bg-accent/10"
          data-tooltip="Attach a workspace file to this conversation"
          aria-label="Attach a workspace file"
          aria-expanded={adding}
        >
          <Plus size={12} />
          Attach
        </button>
      </div>
      {adding && (
        <AttachArtifactForm
          attached={items}
          onPick={(workspaceId, path) =>
            void run(async () => {
              await attach(workspaceId, path);
              setAdding(false);
            })
          }
          onCancel={() => setAdding(false)}
        />
      )}
      {error && <p className="mb-1 text-xs text-red-500">{error}</p>}
      {items.length === 0 ? (
        <p className="text-xs text-muted">
          Files the assistant writes in a workspace land here. Attach one yourself with Attach.
        </p>
      ) : (
        <ul className="space-y-1">
          {items.map((item) => (
            <li key={item.id} className="group relative">
              <button
                type="button"
                onClick={() => openWorkspaceFile(item.workspace_slug, item.path, discussion)}
                className="w-full rounded-md border border-border bg-surface/60 py-1.5 pl-2 pr-6 text-left hover:border-accent/50 hover:bg-surface"
                data-tooltip={`${item.workspace_name}/${item.path}\n${SOURCE_LABEL[item.source]}`}
              >
                <div className="flex items-center gap-1.5">
                  <FileText size={12} className="shrink-0 text-muted" />
                  <span
                    className={`min-w-0 flex-1 truncate ${item.exists ? "" : "text-muted line-through"}`}
                  >
                    {baseName(item.path)}
                  </span>
                  {!item.exists && (
                    <span className="shrink-0 rounded border border-border px-1 text-[10px] text-muted">
                      missing
                    </span>
                  )}
                </div>
                <div className="truncate pl-[18px] text-[11px] text-muted">
                  {item.workspace_name}
                  {dirName(item.path) ? ` · ${dirName(item.path)}` : ""}
                </div>
              </button>
              <button
                type="button"
                onClick={() => void run(() => detach(item.id))}
                className="absolute right-1 top-1 rounded p-0.5 text-muted opacity-0 hover:bg-surface hover:text-text focus:opacity-100 group-hover:opacity-100"
                data-tooltip="Detach from this conversation (the file stays)"
                aria-label={`Detach ${baseName(item.path)}`}
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const MAX_PICK_RESULTS = 30;

// A workspace + file picker. The tree is fetched per workspace, only once the
// user picks one, so opening the form stays cheap.
function AttachArtifactForm({
  attached,
  onPick,
  onCancel,
}: {
  attached: ConversationArtifact[];
  onPick: (workspaceId: number, path: string) => void;
  onCancel: () => void;
}) {
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [workspaceId, setWorkspaceId] = useState<number | null>(null);
  const [files, setFiles] = useState<WorkspaceFileNode[] | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const filterRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    api.workspaces
      .list()
      .then((list) => {
        if (cancelled) return;
        setWorkspaces(list);
        setWorkspaceId((id) => id ?? list[0]?.id ?? null);
      })
      .catch((err) => !cancelled && setError((err as Error).message));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    setFiles(null);
    api.workspaces
      .listFiles(workspaceId)
      .then((nodes) => !cancelled && setFiles(nodes.filter((n) => n.type === "file")))
      .catch((err) => !cancelled && setError((err as Error).message));
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  useEffect(() => filterRef.current?.focus(), []);

  const taken = new Set(
    attached.filter((a) => a.workspace_id === workspaceId).map((a) => a.path),
  );
  const needle = filter.trim().toLowerCase();
  const matches = (files ?? []).filter(
    (f) => !taken.has(f.path) && (!needle || f.path.toLowerCase().includes(needle)),
  );

  if (workspaces !== null && workspaces.length === 0) {
    return <p className="mb-2 text-xs text-muted">No workspaces yet.</p>;
  }

  return (
    <div className="mb-2 space-y-1.5 rounded-md border border-border bg-surface/60 p-2">
      <div className="flex items-center gap-1.5">
        <Paperclip size={12} className="shrink-0 text-muted" />
        <select
          value={workspaceId ?? ""}
          onChange={(e) => setWorkspaceId(Number(e.target.value))}
          className="min-w-0 flex-1 rounded border border-border bg-bg px-1.5 py-1 text-xs"
          aria-label="Workspace"
        >
          {(workspaces ?? []).map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onCancel}
          className="rounded p-0.5 text-muted hover:bg-surface hover:text-text"
          aria-label="Close the file picker"
        >
          <X size={12} />
        </button>
      </div>
      <input
        ref={filterRef}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter files…"
        className="w-full rounded border border-border bg-bg px-1.5 py-1 text-xs"
        aria-label="Filter files"
      />
      {error && <p className="text-xs text-red-500">{error}</p>}
      {files === null && !error ? (
        <p className="text-xs text-muted">Loading files…</p>
      ) : (
        <ul className="max-h-40 overflow-y-auto">
          {matches.slice(0, MAX_PICK_RESULTS).map((f) => (
            <li key={f.path}>
              <button
                type="button"
                onClick={() => workspaceId !== null && onPick(workspaceId, f.path)}
                className="w-full truncate rounded px-1.5 py-0.5 text-left text-xs hover:bg-surface"
                data-tooltip={f.path}
              >
                {f.path}
              </button>
            </li>
          ))}
          {files !== null && matches.length === 0 && (
            <li className="px-1.5 py-0.5 text-xs text-muted">No matching files.</li>
          )}
          {matches.length > MAX_PICK_RESULTS && (
            <li className="px-1.5 py-0.5 text-xs text-muted">
              {matches.length - MAX_PICK_RESULTS} more — keep typing to narrow it down.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
