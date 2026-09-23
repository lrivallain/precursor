import { useEffect, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { eventBus } from "./events";
import { notifyIfUnfocused, notifyNow } from "./notifications";
import {
  navigate,
  parseAppRoute,
  workflowUrl,
  type AppRoute,
} from "./routes";
import { useWorkflowCollection } from "./useWorkflowCollection";

// Workflow run states worth interrupting the user for, and what to say. Only
// terminal-ish transitions notify: a run advancing between steps is noise.
// `awaiting_approval` is the important one — the run is *blocked* on a human,
// so without a notification a background pipeline can wait indefinitely.
const WORKFLOW_NOTICES: Record<string, string> = {
  awaiting_approval: "⏸ Waiting for your approval to continue.",
  completed: "✅ Workflow finished.",
  failed: "⚠️ Workflow failed.",
};

export type WorkflowEditor = { id: number | null } | null;

// Shell state the workflows section reads or drives. The once-registered event
// bus listener below keeps the first render's deps, so it only touches refs and
// state setters.
export interface WorkflowsControllerDeps {
  sidebarMode: SidebarMode;
  atHome: boolean;
  settingsReady: boolean;
  agentsEnabled: boolean;
  notificationsEnabledRef: RefObject<boolean>;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  setAtHome: Dispatch<SetStateAction<boolean>>;
  closeWsRoute: () => void;
  closeMobileNav: () => void;
  confirmLeaveRecording: () => Promise<boolean>;
}

export interface WorkflowsController {
  activeWorkflowId: number | null;
  activeWorkflowRunSeg: string | null;
  setActiveWorkflowRunSeg: Dispatch<SetStateAction<string | null>>;
  workflowEditor: WorkflowEditor;
  setWorkflowEditor: Dispatch<SetStateAction<WorkflowEditor>>;
  workflowCollection: ReturnType<typeof useWorkflowCollection>;
  // Settings-derived flags, passed through so the section components read them
  // from one place.
  settingsReady: boolean;
  agentsEnabled: boolean;
  openWorkflow: (id: number | null) => Promise<void>;
  editWorkflow: (id: number | null) => void;
  refreshWorkflows: () => void;
  syncFromRoute: (r: AppRoute) => void;
  enterOverview: () => void;
  startNew: () => void;
}

export function useWorkflowsController(deps: WorkflowsControllerDeps): WorkflowsController {
  const {
    sidebarMode,
    atHome,
    settingsReady,
    agentsEnabled,
    notificationsEnabledRef,
    setSidebarMode,
    setAtHome,
    closeWsRoute,
    closeMobileNav,
    confirmLeaveRecording,
  } = deps;

  // Selection and editor state are shared by the workflow sidebar and main pane.
  const [activeWorkflowId, setActiveWorkflowId] = useState<number | null>(
    () => parseAppRoute().workflowRef,
  );
  // The run segment shown in the URL (`/run/<n|latest>`). Owned here so the
  // workflow URL effect can write it; kept in sync with the detail view's
  // selected run via onRunChange below.
  const [activeWorkflowRunSeg, setActiveWorkflowRunSeg] = useState<string | null>(
    () => parseAppRoute().workflowRunRef,
  );
  const [workflowReloadKey, setWorkflowReloadKey] = useState(0);
  const [workflowEditor, setWorkflowEditor] = useState<WorkflowEditor>(null);

  const workflowCollection = useWorkflowCollection(
    agentsEnabled && sidebarMode === "workflows" && !atHome,
    workflowReloadKey,
  );

  // Editors are transient; returning to a section never revives an old draft.
  useEffect(() => {
    if (sidebarMode !== "workflows") setWorkflowEditor(null);
  }, [sidebarMode]);

  // Fire a notification when a workflow reaches a state worth interrupting for.
  // A background pipeline is invisible otherwise — and an approval checkpoint
  // *blocks* until someone answers, so parking on one is the single most
  // important thing to surface. Deduped per (workflow, state) so the repeated
  // `workflow.changed` events a run emits don't notify twice for the same
  // transition.
  const workflowNoticeRef = useRef<Map<number, string>>(new Map());
  function maybeNotifyWorkflow(
    workflowId: number | null,
    status: string | null,
    name: string | null,
  ): void {
    if (workflowId == null || !status) return;
    const notice = WORKFLOW_NOTICES[status];
    if (!notice) {
      // Any other state (running, paused…) clears the marker so the *next*
      // completion of this workflow notifies again.
      workflowNoticeRef.current.delete(workflowId);
      return;
    }
    if (workflowNoticeRef.current.get(workflowId) === status) return;
    workflowNoticeRef.current.set(workflowId, status);
    if (!notificationsEnabledRef.current) return;
    const title = name?.trim() || "Workflow";
    // An approval blocks the run, so it's worth showing even when the app is
    // focused — the other outcomes only interrupt an unfocused window.
    const notify = status === "awaiting_approval" ? notifyNow : notifyIfUnfocused;
    notify({ title, body: notice, tag: `precursor-workflow-${workflowId}` });
  }

  // activeWorkflowId (+ run seg) -> /workflows/<id>[/run/<n|latest>] (or
  // /workflows for the gallery). A workflow id change is a navigation (pushState
  // so Back returns to the gallery / previous workflow); a run-seg-only change
  // is a refinement of the same view (replaceState so auto-advancing runs don't
  // spam history).
  const prevWorkflowIdRef = useRef<number | null>(activeWorkflowId);
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "workflows") return;
    const target = workflowUrl(activeWorkflowId, activeWorkflowRunSeg);
    if (window.location.pathname !== target) {
      const idChanged = prevWorkflowIdRef.current !== activeWorkflowId;
      navigate(target, { replace: !idChanged });
    }
    prevWorkflowIdRef.current = activeWorkflowId;
  }, [activeWorkflowId, activeWorkflowRunSeg, sidebarMode, atHome]);

  // Live sync across windows: workflow changes arrive over the shared event
  // bus. `start()` is idempotent, so App starting it too is harmless.
  useEffect(() => {
    eventBus.start();
    const off = eventBus.subscribe((event) => {
      if (event.type === "workflow.changed") {
        // A workflow was created, advanced a step, or finished (possibly in the
        // background via the coordinator). Bump the reload key so the cockpit
        // re-fetches; the WorkflowsSection owns its own collection. The agents
        // controller refreshes the roster from the same event.
        setWorkflowReloadKey((k) => k + 1);
        maybeNotifyWorkflow(
          event.workflow_id ?? null,
          event.workflow_status ?? null,
          event.workflow_name ?? null,
        );
      }
    });
    return () => {
      off();
    };
  }, []);

  async function openWorkflow(id: number | null): Promise<void> {
    if (!(await confirmLeaveRecording())) return;
    setAtHome(false);
    closeWsRoute();
    setWorkflowEditor(null);
    setActiveWorkflowId(id);
    setActiveWorkflowRunSeg(null);
    setSidebarMode("workflows");
    closeMobileNav();
    const target = workflowUrl(id);
    if (window.location.pathname !== target) navigate(target);
  }

  // Open the builder for a workflow (or a new one when `id` is null).
  function editWorkflow(id: number | null): void {
    setWorkflowEditor({ id });
    closeMobileNav();
  }

  // Bump the reload key so the collection re-fetches (e.g. after a restore).
  function refreshWorkflows(): void {
    setWorkflowReloadKey((k) => k + 1);
  }

  // The workflows branch of App's mount + back/forward URL sync.
  function syncFromRoute(r: AppRoute): void {
    setActiveWorkflowId(r.workflowRef);
    setActiveWorkflowRunSeg(r.workflowRunRef);
  }

  // Section navigation always opens the Workflows gallery.
  function enterOverview(): void {
    setActiveWorkflowId(null);
    setActiveWorkflowRunSeg(null);
    setWorkflowEditor(null);
  }

  // Drop the selection and open the builder on a new workflow.
  function startNew(): void {
    setActiveWorkflowId(null);
    setActiveWorkflowRunSeg(null);
    setWorkflowEditor({ id: null });
  }

  return {
    activeWorkflowId,
    activeWorkflowRunSeg,
    setActiveWorkflowRunSeg,
    workflowEditor,
    setWorkflowEditor,
    workflowCollection,
    settingsReady,
    agentsEnabled,
    openWorkflow,
    editWorkflow,
    refreshWorkflows,
    syncFromRoute,
    enterOverview,
    startNew,
  };
}
