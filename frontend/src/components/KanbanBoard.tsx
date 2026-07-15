import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlertCircle, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { api } from "../lib/api";
import { useSettings } from "../lib/settingsStore";
import { Select } from "./Select";
import { IssueLabelChip, IssueStateBadge } from "./IssueTags";
import type { GitHubProject, KanbanBoard as KanbanBoardData, KanbanIssue } from "../lib/types";

// Column shown for issues that have no status value set in the project.
const NO_STATUS_COLUMN = { id: "__none__", name: "No status", color: "#6e7781" };

export function KanbanBoard() {
  const settings = useSettings();
  const githubRepo = settings?.github_repo || "";

  const [projects, setProjects] = useState<GitHubProject[] | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [board, setBoard] = useState<KanbanBoardData | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingBoard, setLoadingBoard] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Optimistic local column assignments while a move request is in flight.
  const [localColumns, setLocalColumns] = useState<Record<string, string | null>>({});

  const loadProjects = useCallback(async () => {
    if (!githubRepo) return;
    setLoadingProjects(true);
    setError(null);
    try {
      const list = await api.github.listProjects();
      setProjects(list);
      if (list.length === 1) setSelectedProjectId(list[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingProjects(false);
    }
  }, [githubRepo]);

  const loadBoard = useCallback(async (projectId: string) => {
    if (!projectId) return;
    setLoadingBoard(true);
    setError(null);
    setLocalColumns({});
    try {
      const data = await api.github.getProjectBoard(projectId);
      setBoard(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingBoard(false);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    if (selectedProjectId) void loadBoard(selectedProjectId);
  }, [selectedProjectId, loadBoard]);

  // ---- Drag & drop --------------------------------------------------------
  const draggingItemId = useRef<string | null>(null);

  function handleDragStart(itemId: string) {
    draggingItemId.current = itemId;
  }

  function handleDragOver(e: { preventDefault: () => void; dataTransfer: DataTransfer }) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }

  async function handleDrop(targetColumnId: string) {
    const itemId = draggingItemId.current;
    draggingItemId.current = null;
    if (!itemId || !board || !board.field_id) return;

    // Find the issue being moved.
    const issue = board.issues.find((i) => i.item_id === itemId);
    if (!issue) return;

    const prevColumnId = localColumns[itemId] ?? issue.column_id;
    if (prevColumnId === targetColumnId) return;

    const realOptionId = targetColumnId === NO_STATUS_COLUMN.id ? "" : targetColumnId;
    if (!realOptionId) return; // can't move to "No status" via the API

    // Optimistic update.
    setLocalColumns((prev) => ({ ...prev, [itemId]: targetColumnId }));

    try {
      await api.github.moveProjectItem(
        board.project_id,
        itemId,
        board.field_id,
        realOptionId,
      );
    } catch {
      // Roll back.
      setLocalColumns((prev) => ({ ...prev, [itemId]: prevColumnId }));
    }
  }

  // ---- Early-out states ---------------------------------------------------
  if (!githubRepo) {
    return (
      <Empty
        icon={<Github size={32} className="text-muted" />}
        title="No GitHub repository configured"
        description="Set a repository in Settings → GitHub to use the kanban board."
      />
    );
  }

  if (loadingProjects) {
    return <Spinner label="Loading projects…" />;
  }

  if (error) {
    return (
      <Empty
        icon={<AlertCircle size={32} className="text-red-500" />}
        title="Could not load board"
        description={error}
        action={
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:bg-bg"
            onClick={() => {
              if (selectedProjectId) void loadBoard(selectedProjectId);
              else void loadProjects();
            }}
          >
            <RefreshCw size={14} /> Retry
          </button>
        }
      />
    );
  }

  if (projects !== null && projects.length === 0) {
    return (
      <Empty
        icon={<Github size={32} className="text-muted" />}
        title="No Projects v2 found"
        description={`The repository ${githubRepo} has no linked Projects v2.`}
      />
    );
  }

  // ---- Project picker when there are multiple projects --------------------
  const projectOptions =
    projects?.map((p) => ({ value: p.id, label: p.title })) ?? [];

  // ---- Board render -------------------------------------------------------
  const columns = board
    ? [...board.columns, NO_STATUS_COLUMN]
    : [];

  function effectiveColumnId(issue: KanbanIssue): string {
    const col = localColumns[issue.item_id] ?? issue.column_id;
    return col ?? NO_STATUS_COLUMN.id;
  }

  return (
    <div className="flex h-full flex-col gap-0">
      {/* Toolbar */}
      <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-2">
        {projectOptions.length > 1 && (
          <Select
            value={selectedProjectId}
            onChange={setSelectedProjectId}
            options={projectOptions}
            placeholder="Pick a project…"
            ariaLabel="Select project"
            size="sm"
          />
        )}
        {board && projects && projects.length === 1 && (
          <span className="text-sm font-medium">{projects[0].title}</span>
        )}
        <div className="flex-1" />
        <button
          type="button"
          disabled={loadingBoard || !selectedProjectId}
          className="flex items-center gap-1.5 rounded border border-border bg-surface px-2.5 py-1 text-sm text-muted hover:bg-bg disabled:opacity-50"
          onClick={() => selectedProjectId && void loadBoard(selectedProjectId)}
          data-tooltip="Refresh board"
          aria-label="Refresh board"
        >
          <RefreshCw size={13} className={loadingBoard ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Board */}
      {loadingBoard ? (
        <Spinner label="Loading board…" />
      ) : !board || !selectedProjectId ? (
        projects !== null && projects.length > 1 ? (
          <Empty
            icon={<Github size={32} className="text-muted" />}
            title="Select a project"
            description="Choose a project from the picker above to load its kanban board."
          />
        ) : null
      ) : (
        <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto p-4">
          {columns.map((col) => {
            const colIssues = board.issues.filter(
              (i) => effectiveColumnId(i) === col.id,
            );
            return (
              <KanbanColumn
                key={col.id}
                id={col.id}
                name={col.name}
                color={col.color}
                issues={colIssues}
                fieldId={board.field_id}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                onDragStart={handleDragStart}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---- Sub-components -------------------------------------------------------

interface ColumnProps {
  id: string;
  name: string;
  color: string;
  issues: KanbanIssue[];
  fieldId: string | null;
  onDragOver: (e: { preventDefault: () => void; dataTransfer: DataTransfer }) => void;
  onDrop: (columnId: string) => void;
  onDragStart: (itemId: string) => void;
}

function KanbanColumn({
  id,
  name,
  color,
  issues,
  fieldId,
  onDragOver,
  onDrop,
  onDragStart,
}: ColumnProps) {
  const [dragOver, setDragOver] = useState(false);

  return (
    <div
      className="flex w-64 shrink-0 flex-col rounded-lg border border-border bg-surface/60"
      onDragOver={(e) => {
        onDragOver(e);
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={() => {
        setDragOver(false);
        onDrop(id);
      }}
      style={dragOver ? { outline: `2px solid ${color}`, outlineOffset: "-2px" } : undefined}
    >
      {/* Column header */}
      <div className="flex items-center gap-2 px-3 py-2.5">
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
          aria-hidden="true"
        />
        <span className="flex-1 truncate text-sm font-medium">{name}</span>
        <span className="rounded-full bg-border px-1.5 py-0.5 text-[11px] text-muted">
          {issues.length}
        </span>
      </div>

      {/* Cards */}
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2 pt-0">
        {issues.map((issue) => (
          <IssueCard
            key={issue.item_id}
            issue={issue}
            draggable={!!fieldId && id !== "__none__"}
            onDragStart={() => onDragStart(issue.item_id)}
          />
        ))}
        {issues.length === 0 && (
          <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted">
            Drop issues here
          </div>
        )}
      </div>
    </div>
  );
}

interface CardProps {
  issue: KanbanIssue;
  draggable: boolean;
  onDragStart: () => void;
}

function IssueCard({ issue, draggable, onDragStart }: CardProps) {
  return (
    <div
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      className={`group flex flex-col gap-1.5 rounded-md border border-border bg-bg p-3 text-left ${
        draggable ? "cursor-grab active:cursor-grabbing" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-1">
        <span className="text-xs text-muted">#{issue.number}</span>
        <a
          href={issue.url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
          aria-label={`Open issue #${issue.number} on GitHub`}
          onClick={(e) => e.stopPropagation()}
        >
          <ExternalLink size={12} className="text-muted hover:text-text" />
        </a>
      </div>
      <p className="text-sm leading-snug">{issue.title}</p>
      <div className="flex flex-wrap items-center gap-1">
        <IssueStateBadge state={issue.state} />
        {issue.labels.map((lbl) => (
          <IssueLabelChip key={lbl.name} label={lbl} />
        ))}
      </div>
    </div>
  );
}

interface EmptyProps {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}

function Empty({ icon, title, description, action }: EmptyProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
      {icon}
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-xs text-xs text-muted">{description}</p>
      {action}
    </div>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted">
      <Loader2 size={16} className="animate-spin" />
      {label}
    </div>
  );
}
