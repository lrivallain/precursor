import { api } from "./api";
import type { Step, StepPlan, StepPlanStatus } from "./types";

export const PLAN_STATUS_LABEL: Record<StepPlanStatus, string> = {
  drafting: "Drafting",
  active: "In progress",
  consolidating: "Consolidating",
  done: "Done",
  cancelled: "Cancelled",
};

export const PLAN_STATUS_DOT: Record<StepPlanStatus, string> = {
  drafting: "bg-slate-400",
  active: "bg-sky-500",
  consolidating: "bg-amber-500",
  done: "bg-emerald-500",
  cancelled: "bg-slate-400",
};

export const PLAN_STATUS_PILL: Record<StepPlanStatus, string> = {
  drafting: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  active: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  consolidating: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  cancelled: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
};

/**
 * What a step looks like to the user. The backend keeps six statuses; the UI
 * collapses them to the four a person acts on, one colour each: slate = not
 * started, blue = being worked on, amber = waiting for you, green = approved.
 */
export type DisplayStatus = "suggested" | "todo" | "working" | "review" | "approved" | "skipped";

export const DISPLAY_STATUS_LABEL: Record<DisplayStatus, string> = {
  suggested: "Suggested",
  todo: "To do",
  working: "In progress",
  review: "To review",
  approved: "Approved",
  skipped: "Skipped",
};

export const DISPLAY_STATUS_PILL: Record<DisplayStatus, string> = {
  suggested: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  todo: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  working: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  review: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  approved: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  skipped: "bg-slate-500/10 text-slate-500 line-through dark:text-slate-400",
};

export const DISPLAY_STATUS_DOT: Record<DisplayStatus, string> = {
  suggested: "bg-violet-500",
  todo: "bg-slate-400",
  working: "bg-sky-500",
  review: "bg-amber-500",
  approved: "bg-emerald-500",
  skipped: "bg-slate-300 dark:bg-slate-600",
};

export function displayStatus(step: Step): DisplayStatus {
  if (step.status === "proposed") return "suggested";
  const status = step.effective_status;
  if (status === "cancelled") return "skipped";
  if (status === "validated") return step.needs_review ? "review" : "approved";
  if (status === "to_validate" || step.artifact.trim() !== "") return "review";
  return status === "in_progress" ? "working" : "todo";
}

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

/** Validated, and not flagged for re-review since (the backend's `is_approved`). */
export function isApprovedStep(step: Step): boolean {
  return step.status === "validated" && !step.needs_review;
}

export function progressOf(node: StepNode): { done: number; total: number } {
  const leaves = workingLeaves(node);
  return { done: leaves.filter((l) => isApprovedStep(l.step)).length, total: leaves.length };
}

export function planProgress(plan: StepPlan): { done: number; total: number } {
  const roots = buildStepTree(plan.steps);
  const leaves = roots.flatMap(workingLeaves);
  return { done: leaves.filter((l) => isApprovedStep(l.step)).length, total: leaves.length };
}

export type PlanStage = "plan" | "work" | "final" | "done";

export const PLAN_STAGES: { id: PlanStage; label: string }[] = [
  { id: "plan", label: "Plan the steps" },
  { id: "work", label: "Work through them" },
  { id: "final", label: "Final output" },
  { id: "done", label: "Done" },
];

function hasFinalOutput(plan: StepPlan): boolean {
  return (plan.final_output ?? "").trim() !== "";
}

/** Where a plan is in its life: planning, working the steps, writing the final output, or done. */
export function planStage(plan: StepPlan): PlanStage {
  if (plan.status === "done") return "done";
  if (plan.status === "drafting" || plan.status === "cancelled") return "plan";
  const { done, total } = planProgress(plan);
  return total > 0 && done === total ? "final" : "work";
}

/** Whether the automated run still has something to do (empty steps to draft, or no final output yet). */
export function autoHasWork(plan: StepPlan): boolean {
  if (plan.automation_mode !== "auto" || (plan.status !== "active" && plan.status !== "consolidating")) {
    return false;
  }
  const leaves = buildStepTree(plan.steps).flatMap(workingLeaves);
  return leaves.some((l) => l.step.artifact.trim() === "") || !hasFinalOutput(plan);
}

export interface PlanNext {
  label: string;
  /** What the button opens: a step id or the final output. */
  target: number | "final";
}

/** The one thing the user should do next on a plan, or null when there is nothing to do. */
export function planNext(plan: StepPlan): PlanNext | null {
  if (plan.status !== "active" && plan.status !== "consolidating") return null;
  const leaves = buildStepTree(plan.steps).flatMap(workingLeaves);
  if (leaves.length === 0) return null;
  const review = leaves.filter((l) => displayStatus(l.step) === "review");
  if (review.length > 0) {
    return { label: `Review next step (${review.length})`, target: review[0].step.id };
  }
  const todo = leaves.find((l) => !isApprovedStep(l.step));
  if (todo) return { label: "Go to the next step", target: todo.step.id };
  if (!hasFinalOutput(plan)) return { label: "Write the final output", target: "final" };
  if (plan.final_stale || plan.final_provisional) return { label: "Regenerate the final output", target: "final" };
  return { label: plan.source_topic_id != null ? "Send to the topic" : "Review the final output", target: "final" };
}

/** The step to show first once a plan is working: the first leaf still to do, else the first step. */
export function stepStartSelection(plan: StepPlan): number | null {
  const flat = flattenTree(buildStepTree(plan.steps)).filter((n) => n.step.status !== "proposed");
  const leaves = flat.filter((n) => acceptedChildren(n).length === 0);
  const todo = leaves.find((n) => !isApprovedStep(n.step) && n.step.status !== "cancelled");
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

export type StepPrimaryKind = "draft" | "approve" | "reopen" | "restore";

export interface StepPrimary {
  kind: StepPrimaryKind;
  label: string;
}

/** The one action that moves a leaf step forward, or null for a group or a suggestion. */
export function primaryAction(node: StepNode): StepPrimary | null {
  const { step } = node;
  if (step.status === "proposed" || isGroup(node)) return null;
  if (step.status === "cancelled") return { kind: "restore", label: "Restore" };
  if (step.artifact.trim() === "") return { kind: "draft", label: "Draft with model" };
  if (step.status === "validated" && !step.needs_review) return { kind: "reopen", label: "Reopen" };
  return { kind: "approve", label: step.needs_review ? "Approve again" : "Approve" };
}

// Starting a plan from a topic crosses sections (topic -> step by step), which the
// section controllers own: like lib/sideChats, this asks for the switch through a
// window event.
export const OPEN_STEP_PLAN_EVENT = "precursor:open-step-plan";

export interface OpenStepPlanDetail {
  planId: number;
}

/** A final output filed into a topic by a plan (see step_plan_llm.topic_note_content). */
export interface StepPlanNote {
  planId: number;
  title: string;
  version: number | null;
  provisional: boolean;
  body: string;
}

const NOTE_HEAD = /^\*\*From step-by-step plan: \[([\s\S]+?)\]\(\/steps\/(\d+)\)(?: · final output v(\d+))?( \(provisional[^)]*\))?\*\*(?:\n\n|$)/;

/** Split a filed final output into its metadata and its content, or null for any other message. */
export function parseStepPlanNote(content: string): StepPlanNote | null {
  const m = NOTE_HEAD.exec(content);
  if (!m) return null;
  return {
    planId: Number(m[2]),
    title: m[1],
    version: m[3] ? Number(m[3]) : null,
    provisional: Boolean(m[4]),
    body: content.slice(m[0].length),
  };
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

/** The server keeps text without leading/trailing newlines; spaces are kept. */
function normalizeText(text: string): string {
  return text.replace(/^\n+|\n+$/g, "");
}

/**
 * Whether `draft` differs from `base` the way the server would see it. Only
 * surrounding newlines are ignored (the server drops them on save), so spaces
 * typed into an empty field still count as an edit.
 */
export function hasUnsavedEdits(draft: string, base: string): boolean {
  return normalizeText(draft) !== normalizeText(base);
}
