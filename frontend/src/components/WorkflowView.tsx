import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArrowLeft,
  Ban,
  CalendarClock,
  Check,
  ChevronDown,
  Coins,
  ChevronRight,
  Copy,
  FileInput,
  GripVertical,
  History,
  ListOrdered,
  Loader2,
  Pause,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  SkipForward,
  ShieldCheck,
  Trash2,
  UserCheck,
  Webhook,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { api } from "../lib/api";
import { CopyableMarkdown } from "./CopyableMarkdown";
import type {
  AgentModelInfo,
  AgentSession,
  Workflow,
  WorkflowRun,
  WorkflowStep,
  WorkflowStepRejectPolicy,
} from "../lib/types";

// How the reject button reads for each checkpoint policy.
const REJECT_ACTION_LABEL: Record<WorkflowStepRejectPolicy, string> = {
  rework: "Send back for rework",
  stop: "Reject & stop the run",
  skip: "Reject & skip ahead",
};

const REJECT_ACTION_HINT: Record<WorkflowStepRejectPolicy, string> = {
  rework: "Rework re-runs the earlier step with your note attached",
  stop: "This checkpoint ends the run when rejected",
  skip: "Rejecting drops this work and continues with the next step",
};
import {
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  RUN_TRIGGER_LABEL,
  STEP_STATE_DOT,
  STEP_STATE_RING,
  WORKFLOW_STATUS_BADGE,
  WORKFLOW_STATUS_LABEL,
  gateRetryLabel,
  isApprovalStep,
  isGateStep,
  runElapsed,
  runProgress,
  scheduleSummary,
  stepLabel,
  stepState,
  formatTokens,
  workflowAwaitsApproval,
  workflowIsActive,
  workflowRelativeTime,
} from "../lib/workflows";
import { WorkflowStepModal } from "./WorkflowStepModal";
import {
  WorkflowStepEditModal,
  draftsFromWorkflow,
  draftsToPayload,
  newDraft,
  type DraftStep,
} from "./WorkflowStepEditor";
import { WorkflowRunTrace } from "./WorkflowRunTrace";
import { WorkflowScheduleEditor } from "./WorkflowScheduleEditor";
import { useConfirm } from "./ConfirmDialog";

interface Props {
  workflow: Workflow;
  /**
   * Run segment from the URL (`/run/<n|latest>`): a run number as a string
   * pins that run, "latest" (or null) follows the live/newest run.
   */
  initialRunSeg?: string | null;
  /** Reports the currently-shown run segment up so the URL can track it. */
  onRunSegChange?: (seg: string | null) => void;
  onBack: () => void;
  onEdit: () => void;
  /** Re-fetch after a lifecycle change so the strip reflects new state. */
  onChanged: (workflow: Workflow) => void;
  onDeleted: () => void;
  onOpenInAgents: (agentId: number) => void;
}

/**
 * Workflow detail: a horizontally scrollable sequence strip of step nodes with
 * live state, the lifecycle control bar (run/pause/resume/cancel), scheduling +
 * webhook config, and a click-through modal into any step's agent run.
 */
/**
 * The gap between two step cards, and the place a new step lands.
 *
 * A bare "+" floating in the gap doesn't say *where* the step goes, so on hover
 * the slot draws a full-height vertical rule between its neighbours — the seam
 * the new card will be spliced into — with the "+" sitting on it.
 */
function InsertSlot({
  onInsert,
  atEnd = false,
  active = false,
}: {
  onInsert: () => void;
  atEnd?: boolean;
  /** True while a dragged card would land in this seam. */
  active?: boolean;
}) {
  if (atEnd) {
    return (
      <button
        type="button"
        onClick={onInsert}
        title="Add a step at the end"
        className={`flex w-10 shrink-0 items-center justify-center self-stretch rounded-lg border border-dashed text-muted transition hover:border-indigo-500/50 hover:text-indigo-500 ${
          active ? "border-indigo-500 bg-indigo-500/10 text-indigo-500" : "border-border"
        }`}
      >
        <Plus size={16} />
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onInsert}
      title="Insert a step here"
      className="group relative flex w-6 shrink-0 items-center justify-center self-stretch text-muted transition hover:text-indigo-500"
    >
      {/* The seam: shown on hover (where a *new* step goes) and while a dragged
          card would land here (where the *moved* step goes). Same signal, so
          both gestures read the same way. */}
      <span
        className={`absolute inset-y-1 left-1/2 w-0.5 -translate-x-1/2 rounded bg-indigo-500 transition ${
          active ? "opacity-100" : "opacity-0 group-hover:opacity-100"
        }`}
        aria-hidden
      />
      <span
        className={`relative flex h-4 w-4 items-center justify-center rounded-full bg-bg ring-1 ring-indigo-500 transition ${
          active ? "opacity-0" : "opacity-0 group-hover:opacity-100"
        }`}
      >
        <Plus size={11} />
      </span>
    </button>
  );
}

/** What a draft card shows before it has been saved (and named) server-side. */
function draftLabel(draft: DraftStep, agents: AgentSession[], idx: number): string {
  if (draft.name.trim()) return draft.name.trim();
  if (draft.kind === "approval") return "Human approval";
  if (draft.mode === "existing") {
    const agent = agents.find((a) => a.id === draft.agentId);
    if (agent) return agent.title;
    return "Pick an agent…";
  }
  return draft.title.trim() || draft.task.trim() || `Step ${idx + 1}`;
}

export function WorkflowView({
  workflow,
  initialRunSeg = null,
  onRunSegChange,
  onBack,
  onEdit,
  onChanged,
  onDeleted,
  onOpenInAgents,
}: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [modalStep, setModalStep] = useState<WorkflowStep | null>(null);
  const [showSchedule, setShowSchedule] = useState(false);
  const [copied, setCopied] = useState(false);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  // Whether the view tracks the live/newest run ("latest" URL) vs. a pinned run
  // number. A deep link to a specific run turns this off so auto-advancing runs
  // don't yank the operator off the run they navigated to.
  const [followLatest, setFollowLatest] = useState(true);
  // A deep-linked run number still waiting for `runs` to load; resolved to an id
  // by the effect below once the matching run appears.
  const pendingRunRef = useRef<string | null>(null);
  // The last run segment we mirrored to/from the URL, used to break the two-way
  // binding loop between this view and App's route state.
  const lastSegRef = useRef<string | null | undefined>(undefined);
  const [showTrace, setShowTrace] = useState(true);
  // Per-run brief: a subject for *this* execution ("analyse /tmp/sales.csv"),
  // fed to every step. Kept in the composer between runs so iterating on the
  // same subject is one click; empty means "run autonomously" (the old default).
  const [brief, setBrief] = useState("");
  const [showBrief, setShowBrief] = useState(false);
  const briefRef = useRef<HTMLTextAreaElement | null>(null);
  // Human approval checkpoint: the note travels with the decision — as a remark
  // on approve, as the rework feedback on reject.
  const [approvalNote, setApprovalNote] = useState("");
  // Inline step authoring. Editing replaces the whole step list server-side (and
  // would null a running workflow's cursor), so it's an explicit mode with an
  // explicit save, and it's refused while a run is in flight.
  const [editingSteps, setEditingSteps] = useState(false);
  const [drafts, setDrafts] = useState<DraftStep[]>([]);
  const [stepAgents, setStepAgents] = useState<AgentSession[]>([]);
  const [stepModels, setStepModels] = useState<AgentModelInfo[]>([]);
  const [savingSteps, setSavingSteps] = useState(false);
  const [stepsError, setStepsError] = useState<string | null>(null);
  // Which draft's settings modal is open, plus the horizontal drag state. The
  // whole card drags; a click without movement still opens its settings modal.
  const [editingDraftKey, setEditingDraftKey] = useState<string | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  // Where the dragged card would land, as an *index between* cards — so the
  // feedback is the seam it drops into rather than a highlight on whichever
  // card happens to be under the cursor (which reads as "replace this one").
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const confirmAction = useConfirm();

  // Load the durable run history. Re-fetched whenever the workflow's live cursor
  // moves (status / current run / step) so the trace timeline tracks execution.
  const loadRuns = useCallback(async () => {
    try {
      setRuns(await api.workflows.runs(workflow.id, 25));
    } catch {
      // Non-fatal: the trace panel simply stays on its last snapshot.
    }
  }, [workflow.id]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns, workflow.status, workflow.current_run_id, workflow.current_step_id]);

  // React to an external run segment (initial mount + browser back/forward, fed
  // down from the route). Ignore echoes of a segment this view itself just
  // emitted so the URL <-> selection binding doesn't loop.
  useEffect(() => {
    const ext = initialRunSeg ?? null;
    if (ext === lastSegRef.current) return;
    lastSegRef.current = ext;
    if (ext == null || ext === "latest") {
      pendingRunRef.current = null;
      setFollowLatest(true);
      return;
    }
    // Pin a specific run number. Resolve now if it's already loaded, otherwise
    // stash it for the resolve effect once `runs` arrives.
    setFollowLatest(false);
    const match = runs.find((r) => String(r.run_number) === ext);
    if (match) {
      pendingRunRef.current = null;
      setSelectedRunId(match.id);
    } else {
      pendingRunRef.current = ext;
    }
    // Only re-run when the external segment changes, not when runs load (the
    // resolve effect handles that case).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialRunSeg]);

  // Resolve a deep-linked run number once its run appears in the loaded history.
  useEffect(() => {
    const want = pendingRunRef.current;
    if (!want) return;
    const match = runs.find((r) => String(r.run_number) === want);
    if (match) {
      pendingRunRef.current = null;
      setSelectedRunId(match.id);
    }
  }, [runs]);

  // Follow the live run. When a **new** run is triggered (the workflow's live
  // `current_run_id` changes to a fresh value), jump to it *only* while tracking
  // latest, so the trace auto-reloads onto the new execution. A pinned deep link
  // stays put. On first load / when the selection goes stale we default to the
  // live/newest run.
  const prevRunIdRef = useRef<number | null>(workflow.current_run_id ?? null);
  useEffect(() => {
    const liveId = workflow.current_run_id ?? null;
    if (liveId != null && liveId !== prevRunIdRef.current) {
      prevRunIdRef.current = liveId;
      if (followLatest) setSelectedRunId(liveId);
      return;
    }
    prevRunIdRef.current = liveId;
    if (pendingRunRef.current) return; // waiting to resolve a deep-linked run
    setSelectedRunId((prev) => {
      // A pinned run stays put as long as it still exists in history.
      if (!followLatest && prev != null && runs.some((r) => r.id === prev)) return prev;
      // Otherwise track the live/newest run (also the first-load default).
      return liveId ?? runs[0]?.id ?? prev ?? null;
    });
  }, [runs, workflow.current_run_id, followLatest]);

  const selectedRun = useMemo(
    () => runs.find((r) => r.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  // Mirror the shown run back into the URL: "latest" while following the live
  // run, else the pinned run number; null when the workflow has no runs.
  useEffect(() => {
    if (!onRunSegChange) return;
    if (pendingRunRef.current) return; // don't overwrite the URL before resolving
    const seg = !selectedRun
      ? runs.length === 0
        ? null
        : lastSegRef.current ?? null
      : followLatest
        ? "latest"
        : String(selectedRun.run_number);
    if (seg === lastSegRef.current) return;
    lastSegRef.current = seg;
    onRunSegChange(seg);
  }, [onRunSegChange, selectedRun, followLatest, runs]);

  // Tick once a second while a run is live so elapsed timers advance smoothly
  // between the coarser SSE-driven parent refreshes.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (workflow.status !== "running") return;
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [workflow.status]);

  // Keep the open modal's step object fresh as the parent re-fetches.
  useEffect(() => {
    if (!modalStep) return;
    const next = workflow.steps.find((s) => s.id === modalStep.id) ?? null;
    setModalStep(next);
  }, [workflow, modalStep]);

  const act = useCallback(
    async (label: string, fn: () => Promise<Workflow>) => {
      setBusy(label);
      try {
        onChanged(await fn());
      } catch {
        // Errors surface via the section's global reload; swallow here.
      } finally {
        setBusy(null);
      }
    },
    [onChanged],
  );

  // --- Draft step manipulation (board-level: order, insert, remove) --------
  function patchDraft(key: string, next: Partial<DraftStep>): void {
    setDrafts((prev) => prev.map((d) => (d.key === key ? { ...d, ...next } : d)));
  }
  function insertDraft(at: number): void {
    setDrafts((prev) => {
      const copy = prev.slice();
      copy.splice(at, 0, newDraft());
      return copy;
    });
  }
  function removeDraft(key: string): void {
    setDrafts((prev) => (prev.length <= 1 ? prev : prev.filter((d) => d.key !== key)));
    setEditingDraftKey((k) => (k === key ? null : k));
  }
  /** Drop the dragged card into the seam currently marked by `dropIndex`. */
  function dropDraft(): void {
    if (!dragKey || dropIndex == null) return;
    setDrafts((prev) => {
      const from = prev.findIndex((d) => d.key === dragKey);
      if (from < 0) return prev;
      const copy = prev.slice();
      const [moved] = copy.splice(from, 1);
      // Removing the card first shifts every later seam left by one.
      copy.splice(dropIndex > from ? dropIndex - 1 : dropIndex, 0, moved);
      return copy;
    });
    setDragKey(null);
    setDropIndex(null);
  }

  /** Enter step-authoring mode, hydrating drafts + the agent/model catalogues. */
  function openStepEditor(): void {
    setDrafts(draftsFromWorkflow(workflow));
    setStepsError(null);
    setEditingSteps(true);
    void api.agents.list().then(setStepAgents).catch(() => {});
    void api.agents.listModels().then(setStepModels).catch(() => {});
  }

  async function saveSteps(): Promise<void> {
    // Saving deletes and recreates every step row, and the run cursor is an FK
    // with ON DELETE SET NULL — writing mid-run would strand the workflow.
    if (workflowIsActive(workflow)) {
      setStepsError("Stop the run before saving step changes.");
      return;
    }
    const payload = draftsToPayload(drafts);
    if (payload == null) {
      setStepsError("Each step needs an agent, or instructions for a new one.");
      return;
    }
    setSavingSteps(true);
    setStepsError(null);
    try {
      onChanged(await api.workflows.update(workflow.id, { steps: payload }));
      setEditingSteps(false);
    } catch {
      setStepsError("Failed to save the steps.");
    } finally {
      setSavingSteps(false);
    }
  }

  const active = workflowIsActive(workflow);

  /** Start a run, optionally with the composer's brief, and follow it live. */
  const startRun = useCallback(
    async (withBrief: boolean) => {
      const input = withBrief ? brief.trim() || null : null;
      // Starting a run is an explicit "show me this one" intent, so unpin any
      // deep-linked historical run and track the new execution.
      pendingRunRef.current = null;
      setFollowLatest(true);
      setShowBrief(false);
      await act("run", () => api.workflows.run(workflow.id, input));
    },
    [act, brief, workflow.id],
  );

  const schedule = scheduleSummary(workflow);
  const progress = selectedRun ? runProgress(selectedRun, workflow.steps.length) : null;
  const progressPct = progress && progress.total > 0
    ? Math.round((progress.done / progress.total) * 100)
    : 0;
  const currentStep = workflow.current_step_id
    ? workflow.steps.find((s) => s.id === workflow.current_step_id) ?? null
    : null;
  const awaitingApproval = workflowAwaitsApproval(workflow);
  const approvalStep = awaitingApproval ? currentStep : null;

  // Which run the step strip reflects. Normally the selected one, so scrolling
  // the run picker replays how the strip looked then. But while a run is *live*,
  // never derive from a different run: between clicking Run and the new run's
  // traces arriving, the selection still points at the previous execution, and
  // painting its finished states would flash last run's colours onto this one.
  // Falling back to null makes stepState use the live cursor, which correctly
  // reads "step 1 active, everything after pending".
  const stripRun =
    workflowIsActive(workflow) &&
    workflow.current_run_id != null &&
    selectedRun?.id !== workflow.current_run_id
      ? null
      : selectedRun;

  const rejectPolicy: WorkflowStepRejectPolicy = approvalStep?.on_reject ?? "rework";

  /** Clear or bounce a human approval checkpoint, carrying the note along.
   *  ``override`` lets the reviewer stop the run regardless of the policy. */
  const decide = useCallback(
    async (verdict: "approve" | "reject", override?: WorkflowStepRejectPolicy) => {
      const note = approvalNote.trim() || null;
      await act(override ? `${verdict}-${override}` : verdict, () =>
        verdict === "approve"
          ? api.workflows.approve(workflow.id, note)
          : api.workflows.reject(workflow.id, note, override ?? null),
      );
      setApprovalNote("");
    },
    [act, approvalNote, workflow.id],
  );

  const webhookPath = workflow.webhook_token
    ? api.workflows.webhookUrl(workflow.webhook_token)
    : null;

  async function removeWebhook(): Promise<void> {
    const ok = await confirmAction({
      title: "Remove webhook?",
      message:
        "The URL stops working immediately. Anything calling it will get a 404 until you mint a new one.",
      confirmLabel: "Remove",
      variant: "danger",
    });
    if (!ok) return;
    await act("hook", () => api.workflows.revokeWebhook(workflow.id));
  }

  async function copyWebhook(): Promise<void> {
    if (!webhookPath) return;
    const url = `${window.location.origin}${webhookPath}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked */
    }
  }

  async function handleArchive(): Promise<void> {
    // Archiving is the reversible sibling of delete: the workflow (and its run
    // history) is kept, just hidden from the gallery until restored.
    const ok = await confirmAction({
      title: "Archive workflow?",
      message: `"${workflow.name}" is hidden from the gallery but keeps its run history. Restore it any time from the archive.`,
      confirmLabel: "Archive",
    });
    if (!ok) return;
    await act("archive", () => api.workflows.archive(workflow.id));
    onDeleted();
  }

  async function handleDelete(): Promise<void> {
    const ok = await confirmAction({
      title: "Delete workflow?",
      message: `"${workflow.name}" will be removed. The underlying agents are left intact.`,
      confirmLabel: "Delete",
      variant: "danger",
    });
    if (!ok) return;
    await api.workflows.remove(workflow.id);
    onDeleted();
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-border px-5 py-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg p-1.5 text-muted transition hover:bg-white/5 hover:text-fg"
          >
            <ArrowLeft size={18} />
          </button>
          {workflow.icon ? (
            <span className="text-2xl">{workflow.icon}</span>
          ) : (
            <WorkflowIcon size={22} className="text-muted" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold text-fg">{workflow.name}</h1>
              <span
                className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  WORKFLOW_STATUS_BADGE[workflow.status]
                }`}
              >
                {workflow.status === "running" && <Loader2 size={10} className="animate-spin" />}
                {WORKFLOW_STATUS_LABEL[workflow.status]}
              </span>
            </div>
            {workflow.description && (
              <p className="truncate text-xs text-muted">{workflow.description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={editingSteps ? () => setEditingSteps(false) : openStepEditor}
            disabled={active && !editingSteps}
            title={
              active
                ? "Stop the run before editing its steps"
                : "Reorder, add and edit steps in place"
            }
            className={`flex items-center gap-1 rounded-lg border px-2 py-1.5 text-xs transition disabled:opacity-40 ${
              editingSteps
                ? "border-indigo-500/50 bg-indigo-500/10 text-indigo-500"
                : "border-border text-muted hover:border-indigo-500/50 hover:text-indigo-500"
            }`}
          >
            <ListOrdered size={13} /> {editingSteps ? "Done" : "Edit steps"}
          </button>
          <button
            type="button"
            onClick={onEdit}
            className="flex items-center gap-1 rounded-lg border border-border px-2 py-1.5 text-xs text-muted transition hover:border-indigo-500/50 hover:text-indigo-500"
          >
            <Pencil size={13} /> Settings
          </button>
          <button
            type="button"
            onClick={() => void handleArchive()}
            title="Archive workflow"
            className="rounded-lg p-1.5 text-muted transition hover:bg-white/5 hover:text-fg"
          >
            <Archive size={16} />
          </button>
          <button
            type="button"
            onClick={() => void handleDelete()}
            className="rounded-lg p-1.5 text-muted transition hover:bg-red-500/10 hover:text-red-500"
            title="Delete workflow"
          >
            <Trash2 size={16} />
          </button>
        </div>

        {/* Human approval checkpoint — the run is parked waiting on a decision.
            Deliberately above the lifecycle bar: it's the only thing that moves
            the pipeline while it's here. */}
        {awaitingApproval && (
          <div className="mt-3 rounded-xl border border-violet-500/40 bg-violet-500/[0.07] p-3">
            <div className="mb-2 flex items-center gap-2">
              <UserCheck size={14} className="text-violet-500" />
              <span className="text-sm font-medium text-fg">
                {approvalStep ? stepLabel(approvalStep) : "Approval needed"}
              </span>
              <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-500">
                Waiting on you
              </span>
            </div>
            {approvalStep?.instructions && (
              <p className="mb-2 whitespace-pre-wrap text-xs leading-relaxed text-fg/80">
                {approvalStep.instructions}
              </p>
            )}
            <textarea
              value={approvalNote}
              onChange={(e) => setApprovalNote(e.target.value.slice(0, 4000))}
              rows={2}
              placeholder="Optional note — required reading if you send it back, so the agent knows what to fix."
              className="w-full resize-y rounded-lg border border-border bg-bg/60 px-3 py-2 text-sm text-fg outline-none transition placeholder:text-muted/70 focus:border-violet-500"
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void decide("approve")}
                disabled={busy != null}
                className="flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-600 disabled:opacity-40"
              >
                {busy === "approve" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Check size={13} />
                )}
                Approve &amp; continue
              </button>
              <button
                type="button"
                onClick={() => void decide("reject")}
                disabled={busy != null}
                className="flex items-center gap-1.5 rounded-lg border border-red-500/40 px-3 py-1.5 text-xs font-medium text-red-500 transition hover:bg-red-500/10 disabled:opacity-40"
              >
                {busy === "reject" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : rejectPolicy === "stop" ? (
                  <Ban size={13} />
                ) : rejectPolicy === "skip" ? (
                  <SkipForward size={13} />
                ) : (
                  <RotateCcw size={13} />
                )}
                {REJECT_ACTION_LABEL[rejectPolicy]}
              </button>
              {/* The checkpoint's declared policy isn't a cage: a reviewer can
                  always end the run outright. */}
              {rejectPolicy !== "stop" && (
                <button
                  type="button"
                  onClick={() => void decide("reject", "stop")}
                  disabled={busy != null}
                  className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition hover:border-red-500/40 hover:text-red-500 disabled:opacity-40"
                >
                  {busy === "reject-stop" ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Ban size={13} />
                  )}
                  Stop the run
                </button>
              )}
              <span className="ml-auto text-[10px] text-muted">
                {REJECT_ACTION_HINT[rejectPolicy]}
              </span>
            </div>
          </div>
        )}

        {/* Lifecycle control bar */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {!active && (
            <div className="flex items-stretch overflow-hidden rounded-lg bg-indigo-500 transition hover:bg-indigo-600">
              <button
                type="button"
                onClick={() => void startRun(true)}
                disabled={busy != null || workflow.steps.length === 0}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
              >
                {busy === "run" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Play size={14} />
                )}
                {workflow.run_count > 0 ? "Run again" : "Run"}
                {brief.trim() && (
                  <span className="rounded bg-white/25 px-1 py-px text-[9px] font-semibold uppercase tracking-wide">
                    briefed
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowBrief((v) => !v);
                  window.setTimeout(() => briefRef.current?.focus(), 0);
                }}
                disabled={workflow.steps.length === 0}
                className="border-l border-white/25 px-1.5 text-white transition hover:bg-white/10 disabled:opacity-40"
                title={showBrief ? "Hide run brief" : "Add a brief for this run"}
                aria-expanded={showBrief}
                aria-label="Run brief"
              >
                <ChevronDown
                  size={14}
                  className={`transition-transform ${showBrief ? "rotate-180" : ""}`}
                />
              </button>
            </div>
          )}
          {workflow.status === "running" && (
            <button
              type="button"
              onClick={() => void act("pause", () => api.workflows.pause(workflow.id))}
              disabled={busy != null}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-fg transition hover:bg-white/5 disabled:opacity-40"
            >
              {busy === "pause" ? <Loader2 size={14} className="animate-spin" /> : <Pause size={14} />}
              Pause
            </button>
          )}
          {workflow.status === "paused" && (
            <button
              type="button"
              onClick={() => void act("resume", () => api.workflows.resume(workflow.id))}
              disabled={busy != null}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-fg transition hover:bg-white/5 disabled:opacity-40"
            >
              {busy === "resume" ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              Resume
            </button>
          )}
          {active && (
            <button
              type="button"
              onClick={() => void act("cancel", () => api.workflows.cancel(workflow.id))}
              disabled={busy != null}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-red-500 transition hover:bg-red-500/10 disabled:opacity-40"
            >
              {busy === "cancel" ? <Loader2 size={14} className="animate-spin" /> : <Ban size={14} />}
              Cancel
            </button>
          )}

          <div className="mx-1 h-5 w-px bg-border" />

          <button
            type="button"
            onClick={() => setShowSchedule((v) => !v)}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition ${
              workflow.schedule_enabled
                ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-500"
                : "border-border text-muted hover:text-fg"
            }`}
          >
            <CalendarClock size={14} />
            {schedule ?? "Schedule"}
          </button>

          {webhookPath ? (
            /* Copy on the control, revoke on its own affordance beside it —
               removing a webhook has nothing to do with recurrence, which is
               where it used to live. */
            <div className="flex items-stretch overflow-hidden rounded-lg border border-border">
              <button
                type="button"
                onClick={() => void copyWebhook()}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-muted transition hover:text-fg"
                title="Copy webhook URL"
              >
                {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
                Webhook
              </button>
              <button
                type="button"
                onClick={() => void removeWebhook()}
                disabled={busy != null}
                className="border-l border-border px-2 text-muted transition hover:bg-red-500/10 hover:text-red-500 disabled:opacity-40"
                title="Remove webhook"
                aria-label="Remove webhook"
              >
                {busy === "hook" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Trash2 size={13} />
                )}
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => void act("hook", () => api.workflows.mintWebhook(workflow.id))}
              disabled={busy != null}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition hover:text-fg disabled:opacity-40"
            >
              {busy === "hook" ? <Loader2 size={14} className="animate-spin" /> : <Webhook size={14} />}
              Add webhook
            </button>
          )}

          {workflow.next_run_at && (
            <span className="ml-auto text-[11px] text-muted">
              Next run {workflowRelativeTime(workflow.next_run_at) || "soon"}
            </span>
          )}
        </div>

        {showBrief && !active && (
          <div className="mt-3 rounded-xl border border-indigo-500/30 bg-indigo-500/[0.04] p-3">
            <div className="mb-2 flex items-center gap-2">
              <FileInput size={13} className="text-indigo-500" />
              <span className="text-xs font-medium text-fg">Brief for this run</span>
              <span className="text-[11px] text-muted">
                Optional — the subject every step works on
              </span>
            </div>
            <textarea
              ref={briefRef}
              value={brief}
              onChange={(e) => setBrief(e.target.value.slice(0, 8000))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void startRun(true);
                }
              }}
              rows={3}
              placeholder="e.g. Analyse /data/q3-sales.csv — focus on the EMEA region and flag anything below target."
              className="w-full resize-y rounded-lg border border-border bg-bg/60 px-3 py-2 text-sm text-fg outline-none transition placeholder:text-muted/70 focus:border-indigo-500"
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void startRun(true)}
                disabled={busy != null || !brief.trim()}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-600 disabled:opacity-40"
              >
                <Play size={13} /> Run with brief
              </button>
              <button
                type="button"
                onClick={() => void startRun(false)}
                disabled={busy != null}
                className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition hover:text-fg disabled:opacity-40"
              >
                Run without
              </button>
              {brief && (
                <button
                  type="button"
                  onClick={() => setBrief("")}
                  className="text-xs text-muted transition hover:text-fg"
                >
                  Clear
                </button>
              )}
              <span className="ml-auto text-[10px] tabular-nums text-muted">
                {brief.length}/8000 · ⌘↵ to run
              </span>
            </div>
          </div>
        )}

        {showSchedule && (
          <div className="mt-3">
            <WorkflowScheduleEditor
              workflow={workflow}
              onSaved={(wf) => {
                onChanged(wf);
                setShowSchedule(false);
              }}
            />
          </div>
        )}
      </div>

      {/* Sequence strip + run trace */}
      <div className="flex-1 overflow-y-auto px-5 py-6">
        {/* Run header — overall progress, live step, elapsed, run picker */}
        {workflow.steps.length > 0 && selectedRun && (
          <div className="mb-5 rounded-2xl border border-border bg-surface/60 px-4 py-3.5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="text-sm font-semibold text-fg">Run #{selectedRun.run_number}</span>
              <span
                className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  RUN_STATUS_BADGE[selectedRun.status] ?? "bg-muted/20 text-muted"
                }`}
              >
                {selectedRun.status === "running" && (
                  <Loader2 size={10} className="animate-spin" />
                )}
                {RUN_STATUS_LABEL[selectedRun.status] ?? selectedRun.status}
              </span>
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-muted">
                {RUN_TRIGGER_LABEL[selectedRun.trigger] ?? selectedRun.trigger}
              </span>
              {runElapsed(selectedRun) && (
                <span className="text-[11px] tabular-nums text-muted">
                  {runElapsed(selectedRun)}
                </span>
              )}
              {selectedRun.started_at && (
                <span className="text-[11px] text-muted">
                  {workflowRelativeTime(selectedRun.finished_at ?? selectedRun.started_at)}
                </span>
              )}
              {selectedRun.total_input_tokens + selectedRun.total_output_tokens > 0 && (
                <span
                  className="flex items-center gap-1 text-[11px] tabular-nums text-muted"
                  title={`${selectedRun.total_input_tokens.toLocaleString()} in · ${selectedRun.total_output_tokens.toLocaleString()} out`}
                >
                  <Coins size={11} />
                  {formatTokens(
                    selectedRun.total_input_tokens + selectedRun.total_output_tokens,
                  )}{" "}
                  tokens
                </span>
              )}

              {/* Run picker (history) */}
              {runs.length > 1 && (
                <div className="ml-auto flex items-center gap-1.5">
                  <History size={13} className="text-muted" />
                  <select
                    value={selectedRunId ?? ""}
                    onChange={(e) => {
                      const id = Number(e.target.value);
                      pendingRunRef.current = null;
                      setFollowLatest(runs[0]?.id === id);
                      setSelectedRunId(id);
                    }}
                    className="rounded-lg border border-border bg-bg/40 px-2 py-1 text-[11px] text-fg outline-none focus:border-indigo-500"
                  >
                    {runs.map((r) => (
                      <option key={r.id} value={r.id}>
                        Run #{r.run_number} · {RUN_STATUS_LABEL[r.status] ?? r.status}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            {/* The brief this run was started with — the run's subject, fed to
                every step. Shown so a past run is self-explanatory. */}
            {selectedRun.input && (
              <div className="mt-3 rounded-xl border border-indigo-500/25 bg-indigo-500/[0.05] px-3 py-2">
                <div className="mb-1 flex items-center gap-1.5">
                  <FileInput size={11} className="text-indigo-500" />
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-indigo-500">
                    Run brief
                  </span>
                </div>
                <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-fg/90">
                  {selectedRun.input}
                </p>
              </div>
            )}

            {/* Progress bar */}
            {progress && progress.total > 0 && (
              <div className="mt-3">
                <div className="mb-1 flex items-center justify-between text-[11px] text-muted">
                  <span>
                    {selectedRun.status === "running" && currentStep
                      ? `Running · ${stepLabel(currentStep)}`
                      : `${progress.done} of ${progress.total} steps`}
                  </span>
                  <span className="tabular-nums">{progressPct}%</span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
                  <div
                    className={`h-full rounded-full transition-all ${
                      selectedRun.status === "failed"
                        ? "bg-red-500"
                        : selectedRun.status === "completed"
                          ? "bg-emerald-500"
                          : "bg-sky-500"
                    }`}
                    style={{ width: `${progressPct}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {editingSteps ? (
          /* Authoring happens on the board: the strip below renders the drafts,
             draggable and with insert slots between them. */
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-indigo-500/30 bg-indigo-500/[0.04] px-3 py-2">
            <ListOrdered size={14} className="text-indigo-500" />
            <span className="text-xs font-medium text-fg">Editing steps</span>
            <span className="text-[11px] text-muted">
              Drag a card to reorder · click one to edit · + inserts a step
            </span>
            <div className="ml-auto flex items-center gap-2">
              {stepsError && <span className="text-[11px] text-red-500">{stepsError}</span>}
              <button
                type="button"
                onClick={() => setEditingSteps(false)}
                className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition hover:text-fg"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void saveSteps()}
                disabled={savingSteps}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-600 disabled:opacity-40"
              >
                {savingSteps ? <Loader2 size={13} className="animate-spin" /> : null}
                Save steps
              </button>
            </div>
          </div>
        ) : null}

        {editingSteps ? null : workflow.steps.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
            <p className="text-sm text-muted">This workflow has no steps yet.</p>
            <button
              type="button"
              onClick={openStepEditor}
              className="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-600"
            >
              Add steps
            </button>
          </div>
        ) : (
          <div className="flex items-stretch gap-1 overflow-x-auto pb-3">
            {workflow.steps.map((step, idx) => {
              const state = stepState(workflow, step, stripRun);
              const gate = isGateStep(step);
              const approval = isApprovalStep(step);
              const retryTo = gateRetryLabel(step);
              const attempts = step.attempt_count ?? 0;
              // The holographic frame draws its own 2px ring at the card's edge,
              // so the card's own border must go transparent or you see both.
              const holo =
                state === "active" &&
                (workflow.status === "running" || workflow.status === "awaiting_approval");
              return (
                <div key={step.id} className="flex items-stretch gap-1">
                  <button
                    type="button"
                    onClick={() => setModalStep(step)}
                    className={`relative flex w-56 shrink-0 flex-col rounded-xl border bg-surface p-3 text-left transition hover:shadow-md ${
                      holo
                        ? "holo-active border-transparent"
                        : `${STEP_STATE_RING[state]} ${gate ? "border-dashed" : ""} ${
                            approval ? "border-dashed border-violet-500/40" : ""
                          }`
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
                        <span className={`h-2 w-2 rounded-full ${STEP_STATE_DOT[state]}`} />
                        {gate
                          ? "Gate"
                          : approval
                            ? "Approval"
                            : step.kind === "inline"
                              ? `Step ${idx + 1} · inline`
                              : `Step ${idx + 1}`}
                      </span>
                      {gate ? (
                        <span className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-medium text-amber-500">
                          <ShieldCheck size={10} />
                          {attempts > 0 ? `retry ${attempts}` : "check"}
                        </span>
                      ) : approval ? (
                        <span className="inline-flex items-center gap-1 rounded bg-violet-500/15 px-1.5 py-0.5 text-[9px] font-medium text-violet-500">
                          <UserCheck size={10} />
                          {state === "active" ? "waiting on you" : "human"}
                        </span>
                      ) : (
                        step.agent && (
                          <span className="rounded bg-white/5 px-1.5 py-0.5 text-[9px] font-medium capitalize text-muted">
                            {step.agent.status.replace(/_/g, " ")}
                          </span>
                        )
                      )}
                    </div>
                    <h3 className="mt-2 line-clamp-2 text-sm font-medium text-fg">
                      {stepLabel(step)}
                    </h3>
                    {gate && (
                      <p className="mt-1 text-[10px] text-amber-500/80">
                        On fail → step {retryTo ?? idx}
                      </p>
                    )}
                    {approval && (
                      <p className="mt-1 text-[10px] text-violet-500/80">
                        {step.on_reject === "stop"
                          ? "Parks for a decision · reject stops the run"
                          : step.on_reject === "skip"
                            ? "Parks for a decision · reject skips ahead"
                            : `Parks for a decision · sent back → step ${retryTo ?? idx}`}
                      </p>
                    )}
                    {step.agent?.progress != null && state === "active" && (
                      <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-white/5">
                        <div
                          className="h-full rounded-full bg-sky-500 transition-all"
                          style={{ width: `${Math.min(100, Math.max(0, step.agent.progress))}%` }}
                        />
                      </div>
                    )}


                    {step.agent?.active_narration && state === "active" && (
                      <p className="mt-1.5 line-clamp-2 text-[11px] text-muted">
                        {step.agent.active_narration}
                      </p>
                    )}
                    {step.agent?.result_summary && state === "done" && (
                      <p className="mt-1.5 line-clamp-2 text-[11px] text-muted">
                        {step.agent.result_summary}
                      </p>
                    )}
                    {!step.agent && !approval && (
                      <p className="mt-1.5 text-[11px] text-red-500">Agent missing</p>
                    )}
                  </button>
                  {idx < workflow.steps.length - 1 && (
                    <div className="flex items-center px-0.5 text-muted">
                      <ChevronRight size={18} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Editable strip — same horizontal pipeline, now authorable: cards drag
            to reorder, the slots between them insert, and a card opens its own
            settings modal. Keeping the layout means the picture of the pipeline
            never changes shape just because you're editing it. */}
        {editingSteps && (
          <div className="flex items-stretch gap-1 overflow-x-auto pb-3">
            {drafts.map((draft, idx) => (
              <div key={draft.key} className="flex items-stretch gap-1">
                <InsertSlot
                  onInsert={() => insertDraft(idx)}
                  active={dropIndex === idx}
                />
                <div
                  draggable
                  onDragStart={() => setDragKey(draft.key)}
                  onDragEnd={() => {
                    setDragKey(null);
                    setDropIndex(null);
                  }}
                  onDragOver={(e) => {
                    if (!dragKey) return;
                    e.preventDefault();
                    // Left half of the card → land before it, right half → after.
                    const box = e.currentTarget.getBoundingClientRect();
                    const next = e.clientX < box.left + box.width / 2 ? idx : idx + 1;
                    if (dropIndex !== next) setDropIndex(next);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    dropDraft();
                  }}
                  className={`relative flex w-56 shrink-0 cursor-grab flex-col rounded-xl border border-dashed bg-surface p-3 text-left transition hover:border-indigo-500/50 active:cursor-grabbing ${
                    dragKey === draft.key ? "opacity-40" : "border-border"
                  }`}
                >
                  <div className="mb-2 flex items-center gap-1.5">
                    <span className="text-muted" title="Drag to reorder" aria-hidden>
                      <GripVertical size={14} />
                    </span>
                    <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                      {draft.kind === "gate"
                        ? "Gate"
                        : draft.kind === "approval"
                          ? "Approval"
                          : draft.kind === "inline"
                            ? `Step ${idx + 1} · inline`
                            : `Step ${idx + 1}`}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeDraft(draft.key)}
                      disabled={drafts.length <= 1}
                      className="ml-auto rounded p-1 text-muted transition hover:bg-red-500/10 hover:text-red-500 disabled:opacity-30"
                      title="Remove step"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => setEditingDraftKey(draft.key)}
                    className="flex flex-1 flex-col text-left"
                  >
                    <h3 className="line-clamp-2 text-sm font-medium text-fg">
                      {draftLabel(draft, stepAgents, idx)}
                    </h3>
                    <span className="mt-1.5 flex items-center gap-1 text-[11px] text-indigo-500">
                      <Pencil size={11} /> Edit step
                    </span>
                  </button>
                </div>
              </div>
            ))}
            <InsertSlot
              onInsert={() => insertDraft(drafts.length)}
              atEnd
              active={dropIndex === drafts.length}
            />
          </div>
        )}

        {/* Selected run's deliverable (final result) */}
        {selectedRun?.result_summary && (
          <div className="mt-6 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-4 py-3">
            <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-500/90">
              <RotateCcw size={12} /> Run #{selectedRun.run_number} result
            </h3>
            <CopyableMarkdown className="text-sm text-fg/90">
              {selectedRun.result_summary}
            </CopyableMarkdown>
          </div>
        )}
        {selectedRun?.error && (
          <div className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">
            {selectedRun.error}
          </div>
        )}

        {/* Run trace — the durable, inspectable per-step history */}
        {selectedRun && selectedRun.step_runs.length > 0 && (
          <div className="mt-6">
            <button
              type="button"
              onClick={() => setShowTrace((v) => !v)}
              className="mb-3 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted transition hover:text-fg"
            >
              <History size={12} />
              Run trace
              <ChevronRight
                size={13}
                className={`transition-transform ${showTrace ? "rotate-90" : ""}`}
              />
            </button>
            {showTrace && (
              <WorkflowRunTrace run={selectedRun} onOpenAgent={onOpenInAgents} />
            )}
          </div>
        )}

        {/* Fallback: legacy last-run summary when no run history exists yet. */}
        {!selectedRun && workflow.result_summary && (
          <div className="mt-6 rounded-xl border border-border bg-bg/40 px-4 py-3">
            <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              <RotateCcw size={12} /> Last run summary
            </h3>
            <CopyableMarkdown className="text-sm text-fg/90">
              {workflow.result_summary}
            </CopyableMarkdown>
          </div>
        )}
        {!selectedRun && workflow.error && (
          <div className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">
            {workflow.error}
          </div>
        )}
      </div>

      {editingSteps && editingDraftKey && (() => {
        const draft = drafts.find((d) => d.key === editingDraftKey);
        if (!draft) return null;
        return (
          <WorkflowStepEditModal
            step={draft}
            index={drafts.findIndex((d) => d.key === editingDraftKey)}
            agents={stepAgents}
            models={stepModels}
            usedAgentIds={
              new Set(
                drafts
                  .filter((d) => d.key !== draft.key && d.mode === "existing")
                  .map((d) => d.agentId),
              )
            }
            onChange={(next) => patchDraft(draft.key, next)}
            onClose={() => setEditingDraftKey(null)}
          />
        );
      })()}

      {modalStep && (
        <WorkflowStepModal
          step={modalStep}
          runs={runs}
          onOpenInAgents={onOpenInAgents}
          onClose={() => setModalStep(null)}
        />
      )}
    </div>
  );
}
