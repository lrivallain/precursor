import { useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  GripVertical,
  IndentDecrease,
  IndentIncrease,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../../lib/api";
import {
  addChildBlocker,
  flattenTree,
  isGroup,
  nestBlocker,
  planMove,
  progressOf,
  siblingIds,
  STEP_STATUS_DOT,
  type MoveKind,
  type MoveTarget,
  type StepNode,
} from "../../lib/stepPlans";
import { useConfirm } from "../ConfirmDialog";
import { ScoreChip } from "./AssessmentPanel";
import { NeedsReviewBadge, StepStatusPill } from "./StepUi";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;
type DropZone = "before" | "after" | "inside";

interface Props {
  planId: number;
  roots: StepNode[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  mutate: Mutate;
  /** Rendered under the selected row (the drafting screen edits in place). */
  renderDetail?: (node: StepNode) => ReactNode;
  /** Shown instead of the tree when there is no step yet. */
  emptyMessage: string;
  footer?: ReactNode;
}

const ACTION_BTN =
  "rounded p-1 text-muted transition hover:bg-bg hover:text-fg disabled:cursor-not-allowed disabled:opacity-40";

/**
 * The step tree: indentation by depth, status pills with the group roll-up,
 * drag & drop to reorder / reparent, and the same moves as buttons (up, down,
 * indent, outdent) for keyboard use. Model suggestions carry Accept / Dismiss.
 */
export function StepTree({
  planId,
  roots,
  selectedId,
  onSelect,
  mutate,
  renderDetail,
  emptyMessage,
  footer,
}: Props) {
  const confirm = useConfirm();
  const [adding, setAdding] = useState<{ parent: number | null } | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);
  const [drop, setDrop] = useState<{ id: number; zone: DropZone } | null>(null);
  const flat = flattenTree(roots);

  function find(id: number): StepNode | undefined {
    return flat.find((n) => n.step.id === id);
  }

  // Where dropping `dragged` on `target` in `zone` would put it, or null when not allowed.
  function resolveDrop(dragged: StepNode, target: StepNode, zone: DropZone): MoveTarget | null {
    if (dragged.step.id === target.step.id) return null;
    if (zone === "inside") {
      if (nestBlocker(target, dragged)) return null;
      const kids = target.children.filter((c) => c.step.id !== dragged.step.id);
      return { parent_step_id: target.step.id, position: kids.length };
    }
    const parentId = target.step.parent_step_id;
    const parent = parentId === null ? null : (find(parentId) ?? null);
    if (nestBlocker(parent, dragged)) return null;
    const sibs = siblingIds(roots, parentId).filter((id) => id !== dragged.step.id);
    const at = sibs.indexOf(target.step.id);
    return { parent_step_id: parentId, position: zone === "before" ? at : at + 1 };
  }

  function zoneFor(e: React.DragEvent<HTMLElement>): DropZone {
    const rect = e.currentTarget.getBoundingClientRect();
    const y = (e.clientY - rect.top) / Math.max(rect.height, 1);
    return y < 0.3 ? "before" : y > 0.7 ? "after" : "inside";
  }

  async function runMove(id: number, target: MoveTarget): Promise<void> {
    await mutate(() => api.stepPlans.moveStep(id, target));
  }

  async function createStep(parent: number | null, title: string): Promise<void> {
    const created = await mutate(() =>
      api.stepPlans.createStep(planId, { title, parent_step_id: parent }),
    );
    if (created) onSelect(created.id);
  }

  async function remove(node: StepNode): Promise<void> {
    const kids = node.children.length;
    const ok = await confirm({
      title: "Delete step?",
      message: `“${node.step.title}”${
        kids ? ` and its ${kids} sub-step${kids === 1 ? "" : "s"}` : ""
      } will be deleted, with their artifacts and chats.`,
      confirmLabel: "Delete",
      variant: "danger",
    });
    if (ok) await mutate(() => api.stepPlans.removeStep(node.step.id));
  }

  function renderNode(node: StepNode): ReactNode {
    const { step, depth } = node;
    const selected = step.id === selectedId;
    const proposed = step.status === "proposed";
    const group = isGroup(node);
    const progress = group ? progressOf(node) : null;
    const dropHere = drop?.id === step.id ? drop.zone : null;
    const dragged = dragId !== null ? find(dragId) : undefined;
    return (
      <li key={step.id}>
        <div
          draggable
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData("text/plain", String(step.id));
            setDragId(step.id);
          }}
          onDragEnd={() => {
            setDragId(null);
            setDrop(null);
          }}
          onDragOver={(e) => {
            if (!dragged) return;
            const zone = zoneFor(e);
            if (!resolveDrop(dragged, node, zone)) {
              e.dataTransfer.dropEffect = "none";
              if (drop) setDrop(null);
              return;
            }
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            if (drop?.id !== step.id || drop.zone !== zone) setDrop({ id: step.id, zone });
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDrop(null);
          }}
          onDrop={(e) => {
            e.preventDefault();
            const zone = zoneFor(e);
            setDrop(null);
            setDragId(null);
            if (!dragged) return;
            const target = resolveDrop(dragged, node, zone);
            if (target) void runMove(dragged.step.id, target);
          }}
          className={`group relative rounded-lg border ${
            selected ? "border-accent/60 bg-accent/5" : "border-transparent hover:bg-surface"
          } ${dragId === step.id ? "opacity-40" : ""} ${
            dropHere === "inside" ? "ring-2 ring-accent/60" : ""
          }`}
          style={{ marginLeft: depth * 16 }}
          data-testid={`step-row-${step.id}`}
        >
          {dropHere === "before" && (
            <span className="pointer-events-none absolute inset-x-0 -top-px h-0.5 rounded bg-accent" />
          )}
          {dropHere === "after" && (
            <span className="pointer-events-none absolute inset-x-0 -bottom-px h-0.5 rounded bg-accent" />
          )}
          <div className="flex items-center gap-1.5 px-1.5 py-1.5">
            <GripVertical
              size={14}
              className="shrink-0 cursor-grab text-muted/50 group-hover:text-muted"
              aria-hidden
            />
            <button
              type="button"
              onClick={() => onSelect(step.id)}
              aria-current={selected ? "true" : undefined}
              className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
            >
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${STEP_STATUS_DOT[step.effective_status]}`}
                aria-hidden
              />
              <span
                className={`min-w-0 flex-1 truncate ${
                  step.effective_status === "cancelled" ? "text-muted line-through" : ""
                } ${group ? "font-medium" : ""}`}
              >
                {step.title}
              </span>
              {progress && (
                <span
                  className="shrink-0 text-[11px] tabular-nums text-muted"
                  data-tooltip={`${progress.done} of ${progress.total} sub-steps validated`}
                >
                  {progress.done}/{progress.total}
                </span>
              )}
              {step.needs_review && <NeedsReviewBadge />}
              {step.assessment && <ScoreChip assessment={step.assessment} stale={step.assessment_stale} />}
              <StepStatusPill status={step.effective_status} />
            </button>
            {proposed && (
              <>
                <button
                  type="button"
                  className="rounded p-1 text-emerald-600 hover:bg-emerald-500/15 dark:text-emerald-400"
                  aria-label={`Accept suggestion “${step.title}”`}
                  data-tooltip="Accept this suggestion"
                  onClick={() => void mutate(() => api.stepPlans.acceptStep(step.id))}
                >
                  <Check size={14} />
                </button>
                <button
                  type="button"
                  className="rounded p-1 text-muted hover:bg-red-500/15 hover:text-red-500"
                  aria-label={`Dismiss suggestion “${step.title}”`}
                  data-tooltip="Dismiss this suggestion"
                  onClick={() => void mutate(() => api.stepPlans.removeStep(step.id))}
                >
                  <X size={14} />
                </button>
              </>
            )}
          </div>
          {selected && <MoveBar node={node} roots={roots} onMove={runMove} onAdd={() => setAdding({ parent: step.id })} onRemove={() => void remove(node)} />}
          {selected && renderDetail && <div className="px-2 pb-2">{renderDetail(node)}</div>}
        </div>
        {adding?.parent === step.id && (
          <div style={{ marginLeft: (depth + 1) * 16 }}>
            <AddStepRow
              onCancel={() => setAdding(null)}
              onAdd={(title) => {
                setAdding(null);
                void createStep(step.id, title);
              }}
            />
          </div>
        )}
        {node.children.length > 0 && <ul className="space-y-0.5">{node.children.map(renderNode)}</ul>}
      </li>
    );
  }

  return (
    <div className="space-y-2">
      {roots.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted">
          {emptyMessage}
        </p>
      ) : (
        <ul className="space-y-0.5" aria-label="Steps">
          {roots.map(renderNode)}
        </ul>
      )}
      {adding?.parent === null ? (
        <AddStepRow
          onCancel={() => setAdding(null)}
          onAdd={(title) => {
            setAdding(null);
            void createStep(null, title);
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => setAdding({ parent: null })}
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface hover:text-fg"
        >
          <Plus size={14} /> Add step
        </button>
      )}
      {footer}
    </div>
  );
}

function MoveBar({
  node,
  roots,
  onMove,
  onAdd,
  onRemove,
}: {
  node: StepNode;
  roots: StepNode[];
  onMove: (id: number, target: MoveTarget) => Promise<void>;
  onAdd: () => void;
  onRemove: () => void;
}) {
  const buttons: { kind: MoveKind; label: string; icon: ReactNode }[] = [
    { kind: "up", label: "Move up", icon: <ArrowUp size={14} /> },
    { kind: "down", label: "Move down", icon: <ArrowDown size={14} /> },
    { kind: "indent", label: "Nest under the previous step", icon: <IndentIncrease size={14} /> },
    { kind: "outdent", label: "Move out one level", icon: <IndentDecrease size={14} /> },
  ];
  const addBlocker = addChildBlocker(node);
  return (
    <div className="flex items-center gap-0.5 px-2 pb-1.5 pl-7" role="toolbar" aria-label="Step actions">
      {buttons.map(({ kind, label, icon }) => {
        const result = planMove(roots, node, kind);
        const blocked = "blocked" in result ? result.blocked : null;
        return (
          <button
            key={kind}
            type="button"
            className={ACTION_BTN}
            aria-label={label}
            data-tooltip={blocked ? `${label}\n${blocked}` : label}
            disabled={blocked !== null}
            onClick={() => "target" in result && void onMove(node.step.id, result.target)}
          >
            {icon}
          </button>
        );
      })}
      <span className="mx-1 h-4 w-px bg-border" aria-hidden />
      <button
        type="button"
        className={ACTION_BTN}
        aria-label="Add sub-step"
        data-tooltip={addBlocker ? `Add sub-step\n${addBlocker}` : "Add sub-step"}
        disabled={addBlocker !== null}
        onClick={onAdd}
      >
        <Plus size={14} />
      </button>
      {node.step.status !== "proposed" && (
        <button
          type="button"
          className={`${ACTION_BTN} hover:!text-red-500`}
          aria-label="Delete step"
          data-tooltip="Delete step"
          onClick={onRemove}
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}

function AddStepRow({ onAdd, onCancel }: { onAdd: (title: string) => void; onCancel: () => void }) {
  const [title, setTitle] = useState("");
  const doneRef = useRef(false);
  function submit(): void {
    if (doneRef.current) return;
    const t = title.trim();
    doneRef.current = true;
    if (t) onAdd(t);
    else onCancel();
  }
  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-accent/60 bg-bg px-2 py-1">
      <ChevronRight size={14} className="shrink-0 text-muted" aria-hidden />
      <input
        autoFocus
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={submit}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
          if (e.key === "Escape") {
            doneRef.current = true;
            onCancel();
          }
        }}
        maxLength={255}
        placeholder="Step title — Enter to add, Esc to cancel"
        aria-label="New step title"
        className="min-w-0 flex-1 bg-transparent py-1 text-sm outline-none"
      />
    </div>
  );
}
