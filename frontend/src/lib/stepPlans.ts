import { api } from "./api";
import type { SettableStepStatus, Step, StepPlan, StepPlanStatus, StepStatus } from "./types";

export const PLAN_STATUS_LABEL: Record<StepPlanStatus, string> = {
  drafting: "Drafting",
  active: "In progress",
  consolidating: "Consolidating",
  done: "Done",
  cancelled: "Cancelled",
};

export const PLAN_STATUS_DOT: Record<StepPlanStatus, string> = {
  drafting: "bg-slate-400",
  active: "bg-teal-500",
  consolidating: "bg-amber-500",
  done: "bg-emerald-500",
  cancelled: "bg-slate-400",
};

export const PLAN_STATUS_PILL: Record<StepPlanStatus, string> = {
  drafting: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  active: "bg-teal-500/15 text-teal-700 dark:text-teal-300",
  consolidating: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  cancelled: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
};

export const STEP_STATUS_LABEL: Record<StepStatus, string> = {
  proposed: "Suggested",
  open: "Open",
  in_progress: "In progress",
  to_validate: "To validate",
  validated: "Validated",
  cancelled: "Cancelled",
};

export const STEP_STATUS_PILL: Record<StepStatus, string> = {
  proposed: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  open: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  in_progress: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  to_validate: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  validated: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  cancelled: "bg-slate-500/10 text-slate-500 line-through dark:text-slate-400",
};

export const STEP_STATUS_DOT: Record<StepStatus, string> = {
  proposed: "bg-violet-500",
  open: "bg-slate-400",
  in_progress: "bg-sky-500",
  to_validate: "bg-amber-500",
  validated: "bg-emerald-500",
  cancelled: "bg-slate-300 dark:bg-slate-600",
};

/** Backend caps (services/step_plans.py); mirrored to disable actions early. */
export const MAX_STEP_DEPTH = 6;

export interface StepNode {
  step: Step;
  depth: number;
  children: StepNode[];
}

/** Children first by position (id breaks ties), so the tree order is stable. */
function byPosition(a: Step, b: Step): number {
  return a.position - b.position || a.id - b.id;
}

export function buildStepTree(steps: Step[]): StepNode[] {
  const byParent = new Map<number | null, Step[]>();
  const ids = new Set(steps.map((s) => s.id));
  for (const s of steps) {
    // An orphan (parent missing from the payload) is shown at the root.
    const key = s.parent_step_id != null && ids.has(s.parent_step_id) ? s.parent_step_id : null;
    const list = byParent.get(key) ?? [];
    list.push(s);
    byParent.set(key, list);
  }
  const make = (parent: number | null, depth: number): StepNode[] =>
    (byParent.get(parent) ?? [])
      .sort(byPosition)
      .map((step) => ({ step, depth, children: make(step.id, depth + 1) }));
  return make(null, 0);
}

export function flattenTree(nodes: StepNode[]): StepNode[] {
  return nodes.flatMap((n) => [n, ...flattenTree(n.children)]);
}

/** Accepted (non-proposed) children — what makes a step a group. */
export function acceptedChildren(node: StepNode): StepNode[] {
  return node.children.filter((c) => c.step.status !== "proposed");
}

export function isGroup(node: StepNode): boolean {
  return acceptedChildren(node).length > 0;
}

/** Working leaves under a node: accepted, not cancelled, no accepted children. */
export function workingLeaves(node: StepNode): StepNode[] {
  if (node.step.status === "proposed" || node.step.status === "cancelled") return [];
  const kids = acceptedChildren(node);
  if (kids.length === 0) return [node];
  return kids.flatMap(workingLeaves);
}

export function progressOf(node: StepNode): { done: number; total: number } {
  const leaves = workingLeaves(node);
  return { done: leaves.filter((l) => l.step.status === "validated").length, total: leaves.length };
}

export function planProgress(plan: StepPlan): { done: number; total: number } {
  const roots = buildStepTree(plan.steps);
  const leaves = roots.flatMap(workingLeaves);
  return { done: leaves.filter((l) => l.step.status === "validated").length, total: leaves.length };
}

/** The step to show first once a plan is working: the first leaf still to do, else the first step. */
export function stepStartSelection(plan: StepPlan): number | null {
  const flat = flattenTree(buildStepTree(plan.steps)).filter((n) => n.step.status !== "proposed");
  const leaves = flat.filter((n) => acceptedChildren(n).length === 0);
  const todo = leaves.find((n) => n.step.status !== "validated" && n.step.status !== "cancelled");
  return (todo ?? flat[0])?.step.id ?? null;
}

export function descendantIds(node: StepNode): Set<number> {
  return new Set(flattenTree(node.children).map((n) => n.step.id));
}

export function subtreeHeight(node: StepNode): number {
  return 1 + Math.max(0, ...node.children.map(subtreeHeight));
}

/** Sibling ids under `parentId`, in tree order. */
export function siblingIds(roots: StepNode[], parentId: number | null): number[] {
  if (parentId === null) return roots.map((n) => n.step.id);
  const parent = flattenTree(roots).find((n) => n.step.id === parentId);
  return parent ? parent.children.map((n) => n.step.id) : [];
}

export interface MoveTarget {
  parent_step_id: number | null;
  /** Index among the new siblings, not counting the moved step itself. */
  position: number;
}

/** Why `moving` can't sit under `parent` (null = the root), or null when it can. */
export function nestBlocker(parent: StepNode | null, moving: StepNode): string | null {
  if (parent === null) return null;
  if (parent.step.id === moving.step.id || descendantIds(moving).has(parent.step.id)) {
    return "A step can't go inside its own sub-steps.";
  }
  if (parent.step.artifact.trim() && parent.step.id !== moving.step.parent_step_id) {
    return "That step already has an artifact, so it can't become a group.";
  }
  if (parent.depth + subtreeHeight(moving) + 1 > MAX_STEP_DEPTH) return "Steps can't nest that deep.";
  return null;
}

/** Whether `parent` may receive a brand-new child (add sub-step). */
export function addChildBlocker(parent: StepNode): string | null {
  if (parent.step.artifact.trim()) return "This step already has an artifact, so it can't be split.";
  if (parent.depth + 1 >= MAX_STEP_DEPTH) return "Steps can't nest that deep.";
  return null;
}

export type MoveKind = "up" | "down" | "indent" | "outdent";

/** The `/move` payload for a keyboard-accessible reorder, or the reason it's unavailable. */
export function planMove(
  roots: StepNode[],
  node: StepNode,
  kind: MoveKind,
): { target: MoveTarget } | { blocked: string } {
  const flat = flattenTree(roots);
  const parentId = node.step.parent_step_id;
  const sibs = siblingIds(roots, parentId);
  const idx = sibs.indexOf(node.step.id);
  if (kind === "up") {
    return idx > 0
      ? { target: { parent_step_id: parentId, position: idx - 1 } }
      : { blocked: "Already first" };
  }
  if (kind === "down") {
    return idx >= 0 && idx < sibs.length - 1
      ? { target: { parent_step_id: parentId, position: idx + 1 } }
      : { blocked: "Already last" };
  }
  if (kind === "indent") {
    if (idx <= 0) return { blocked: "Needs a previous step to nest under" };
    const prev = flat.find((n) => n.step.id === sibs[idx - 1]);
    if (!prev) return { blocked: "Needs a previous step to nest under" };
    const blocker = nestBlocker(prev, node);
    return blocker
      ? { blocked: blocker }
      : { target: { parent_step_id: prev.step.id, position: prev.children.length } };
  }
  if (parentId === null) return { blocked: "Already at the top level" };
  const parent = flat.find((n) => n.step.id === parentId);
  if (!parent) return { blocked: "Already at the top level" };
  const grandId = parent.step.parent_step_id;
  const parentIdx = siblingIds(roots, grandId).indexOf(parent.step.id);
  return { target: { parent_step_id: grandId, position: parentIdx + 1 } };
}

export interface StatusAction {
  status: SettableStepStatus;
  label: string;
  disabledReason?: string;
}

/** Status actions offered for a step, with the reason when one is blocked. */
export function statusActions(node: StepNode): StatusAction[] {
  const { step } = node;
  const group = isGroup(node);
  const hasArtifact = step.artifact.trim().length > 0;
  const needsArtifact = hasArtifact ? undefined : "Write or generate the artifact first.";
  if (step.status === "proposed") return [];
  if (group) {
    return step.effective_status === "cancelled"
      ? [{ status: "open", label: "Reopen" }]
      : [{ status: "cancelled", label: "Cancel group" }];
  }
  switch (step.status) {
    case "open":
      return [
        { status: "in_progress", label: "Start" },
        { status: "to_validate", label: "Mark to validate", disabledReason: needsArtifact },
        { status: "cancelled", label: "Cancel" },
      ];
    case "in_progress":
      return [
        { status: "to_validate", label: "Mark to validate", disabledReason: needsArtifact },
        { status: "validated", label: "Validate", disabledReason: needsArtifact },
        { status: "cancelled", label: "Cancel" },
      ];
    case "to_validate":
      return [
        { status: "validated", label: "Validate" },
        { status: "in_progress", label: "Back to work" },
        { status: "cancelled", label: "Cancel" },
      ];
    case "validated":
      return [
        { status: "in_progress", label: "Reopen" },
        { status: "cancelled", label: "Cancel" },
      ];
    case "cancelled":
      return [{ status: "open", label: "Reopen" }];
    default:
      return [];
  }
}

// Starting a plan from a topic crosses sections (topic -> step by step), which the
// section controllers own: like lib/sideChats, this asks for the switch through a
// window event.
export const OPEN_STEP_PLAN_EVENT = "precursor:open-step-plan";

export interface OpenStepPlanDetail {
  planId: number;
}

/** Switch to Step by step and open plan `planId`. */
export function openStepPlan(planId: number): void {
  window.dispatchEvent(
    new CustomEvent<OpenStepPlanDetail>(OPEN_STEP_PLAN_EVENT, { detail: { planId } }),
  );
}

/**
 * Split a topic's latest ask into a step plan (the model proposes the first
 * steps, so this is slow) and open it. When the model fails the server still
 * keeps the (empty) plan but answers 502, so the caller reports the error and
 * the plan shows up in the section's list.
 */
export async function startStepPlanFromTopic(
  topicId: number,
  sourceText?: string,
): Promise<StepPlan> {
  const text = sourceText?.trim();
  const plan = await api.stepPlans.create({
    topic_id: topicId,
    propose: true,
    ...(text ? { source_text: text } : {}),
  });
  openStepPlan(plan.id);
  return plan;
}

/** Whether `draft` differs from `base` in a way worth warning about (blank ≈ blank). */
export function hasUnsavedEdits(draft: string, base: string): boolean {
  if (draft === base) return false;
  return !(draft.trim() === "" && base.trim() === "");
}
