import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, GitPullRequest, RefreshCw } from "lucide-react";
import type { ProjectBoard, ProjectCard } from "../lib/types";
import { api, apiErrorMessage } from "../lib/api";
import { IssueLabelChip } from "./IssueTags";
import { IssuePreviewModal } from "./IssuePreviewModal";

interface KanbanBoardProps {
  projectId: string;
  /** Configured repo, used to preview cards that don't carry their own repo. */
  fallbackRepo: string;
  /** Open the Precursor topic linked to an issue (from the preview modal). */
  onOpenTopic?: (topicId: number) => void;
}

// Synthetic column holding items that have no Status assigned. It only accepts
// cards as a *source* — dropping onto it is a no-op because clearing a status
// isn't part of the drag-drop contract.
const NO_STATUS = "__no_status__";

interface Column {
  id: string;
  name: string;
  droppable: boolean;
  cards: ProjectCard[];
}

/**
 * Drag-and-drop kanban board for a GitHub Projects v2 project. Columns are
 * auto-generated from the project's Status single-select field. Dropping a card
 * onto a column optimistically updates local state and calls the backend; a
 * failed update rolls the board back and surfaces the error.
 */
export function KanbanBoard({ projectId, fallbackRepo, onOpenTopic }: KanbanBoardProps) {
  const [board, setBoard] = useState<ProjectBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<string | null>(null);
  // The card whose issue preview is open (null when the modal is closed).
  const [previewCard, setPreviewCard] = useState<ProjectCard | null>(null);
  // Mirror the drag id so the drop handler never reads a stale closure value.
  const dragIdRef = useRef<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.github.projectBoard(projectId);
      setBoard(data);
    } catch (e) {
      setError(apiErrorMessage(e, "Failed to load project board"));
      setBoard(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const columns = useMemo<Column[]>(() => {
    if (!board) return [];
    const options = board.status_field?.options ?? [];
    const byOption = new Map<string, ProjectCard[]>();
    for (const opt of options) byOption.set(opt.id, []);
    const noStatus: ProjectCard[] = [];
    for (const card of board.items) {
      const key = card.status_option_id;
      if (key && byOption.has(key)) byOption.get(key)!.push(card);
      else noStatus.push(card);
    }
    const cols: Column[] = options.map((opt) => ({
      id: opt.id,
      name: opt.name,
      droppable: true,
      cards: byOption.get(opt.id) ?? [],
    }));
    if (noStatus.length > 0) {
      cols.unshift({ id: NO_STATUS, name: "No Status", droppable: false, cards: noStatus });
    }
    return cols;
  }, [board]);

  const setDrag = useCallback((id: string | null) => {
    dragIdRef.current = id;
    setDragId(id);
  }, []);

  const handleDrop = useCallback(
    async (targetOptionId: string) => {
      setOverCol(null);
      const itemId = dragIdRef.current;
      setDrag(null);
      if (!itemId || !board?.status_field) return;
      const fieldId = board.status_field.id;
      const option = board.status_field.options.find((o) => o.id === targetOptionId);
      const card = board.items.find((c) => c.id === itemId);
      if (!option || !card || card.status_option_id === targetOptionId) return;

      const snapshot = board;
      // Optimistic move.
      setBoard({
        ...board,
        items: board.items.map((c) =>
          c.id === itemId
            ? { ...c, status_option_id: option.id, status_name: option.name }
            : c,
        ),
      });
      setActionError(null);
      try {
        await api.github.setProjectItemStatus(projectId, itemId, {
          field_id: fieldId,
          option_id: option.id,
        });
      } catch (e) {
        // Roll back to the pre-drop board and surface the failure.
        setBoard(snapshot);
        setActionError(
          `Couldn't move "${card.title}": ${apiErrorMessage(e, "update failed")}`,
        );
      }
    },
    [board, projectId, setDrag],
  );

  if (loading && !board) {
    return <CenteredMessage>Loading board…</CenteredMessage>;
  }
  if (error) {
    return (
      <CenteredMessage>
        <div className="flex flex-col items-center gap-3 text-center">
          <AlertTriangle size={28} className="text-red-500" />
          <p className="max-w-md text-sm text-muted">{error}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded border border-border px-3 py-1.5 text-sm hover:bg-surface"
          >
            Retry
          </button>
        </div>
      </CenteredMessage>
    );
  }
  if (!board) return null;

  if (!board.status_field || board.status_field.options.length === 0) {
    return (
      <CenteredMessage>
        This project has no <span className="mx-1 font-medium">Status</span> field to build columns
        from.
      </CenteredMessage>
    );
  }

  const cardCount = board.items.length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2">
        <span className="text-sm text-muted">
          {cardCount} {cardCount === 1 ? "item" : "items"}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void load()}
            className="rounded p-1.5 text-muted hover:bg-surface hover:text-text"
            aria-label="Refresh board"
            data-tooltip="Refresh board"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : undefined} />
          </button>
          {board.url && (
            <a
              href={board.url}
              target="_blank"
              rel="noreferrer"
              className="rounded p-1.5 text-muted hover:bg-surface hover:text-text"
              aria-label="Open project on GitHub"
              data-tooltip="Open on GitHub"
            >
              <ExternalLink size={15} />
            </a>
          )}
        </div>
      </div>

      {actionError && (
        <div className="flex items-center gap-2 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-500">
          <AlertTriangle size={14} className="shrink-0" />
          <span className="flex-1 truncate">{actionError}</span>
          <button
            type="button"
            onClick={() => setActionError(null)}
            className="shrink-0 rounded px-1.5 py-0.5 text-xs hover:bg-red-500/20"
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="flex-1 overflow-x-auto">
        <div className="flex h-full items-stretch gap-3 p-3">
          {columns.map((col) => (
            <div
              key={col.id}
              className={`flex w-72 shrink-0 flex-col overflow-hidden rounded-lg border bg-surface/40 ${
                overCol === col.id && col.droppable
                  ? "border-accent ring-1 ring-accent/40"
                  : "border-border"
              }`}
              onDragOver={
                col.droppable
                  ? (e) => {
                      e.preventDefault();
                      if (overCol !== col.id) setOverCol(col.id);
                    }
                  : undefined
              }
              onDragLeave={
                col.droppable
                  ? (e) => {
                      // Only clear when the cursor truly leaves the column.
                      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
                        setOverCol((c) => (c === col.id ? null : c));
                      }
                    }
                  : undefined
              }
              onDrop={col.droppable ? () => void handleDrop(col.id) : undefined}
            >
              <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
                <span className="truncate text-sm font-medium">{col.name}</span>
                <span className="shrink-0 rounded-full bg-border/60 px-2 py-0.5 text-xs text-muted">
                  {col.cards.length}
                </span>
              </div>
              <div className="flex-1 space-y-2 overflow-y-auto p-2">
                {col.cards.map((card) => (
                  <KanbanCard
                    key={card.id}
                    card={card}
                    dragging={dragId === card.id}
                    onDragStart={() => setDrag(card.id)}
                    onDragEnd={() => {
                      setDrag(null);
                      setOverCol(null);
                    }}
                    onOpen={() => setPreviewCard(card)}
                  />
                ))}
                {col.cards.length === 0 && (
                  <div className="px-2 py-6 text-center text-xs text-muted">Empty</div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {previewCard && (
        <IssuePreviewModal
          card={previewCard}
          fallbackRepo={fallbackRepo}
          onOpenTopic={onOpenTopic}
          onClose={() => setPreviewCard(null)}
        />
      )}
    </div>
  );
}

interface KanbanCardProps {
  card: ProjectCard;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onOpen: () => void;
}

function KanbanCard({ card, dragging, onDragStart, onDragEnd, onOpen }: KanbanCardProps) {
  const isPr = card.type === "pull_request";
  return (
    <article
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className={`cursor-grab rounded-md border border-border bg-bg p-2.5 text-sm shadow-sm transition active:cursor-grabbing ${
        dragging ? "opacity-40" : "hover:border-accent/50"
      }`}
    >
      <div className="mb-1 flex items-center gap-1.5 text-xs text-muted">
        {isPr && <GitPullRequest size={12} className="shrink-0" />}
        {card.number != null && (
          <span>
            {isPr ? "PR" : ""}#{card.number}
          </span>
        )}
        {card.state && <StateDot state={card.state} />}
      </div>
      <span className="line-clamp-3 font-medium">{card.title}</span>
      {card.labels.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {card.labels.map((label) => (
            <IssueLabelChip key={label.name} label={label} />
          ))}
        </div>
      )}
    </article>
  );
}

function StateDot({ state }: { state: string }) {
  const s = state.toLowerCase();
  const cls =
    s === "open"
      ? "bg-green-500"
      : s === "merged"
        ? "bg-purple-500"
        : "bg-muted";
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`h-2 w-2 rounded-full ${cls}`} />
      <span className="uppercase tracking-wide">{s}</span>
    </span>
  );
}

function CenteredMessage({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-sm text-muted">{children}</div>
  );
}
