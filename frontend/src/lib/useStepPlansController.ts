import { useCallback, useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { api, apiErrorMessage } from "./api";
import { eventBus } from "./events";
import { navigate, parseAppRoute, stepPlanUrl, type AppRoute } from "./routes";
import { OPEN_STEP_PLAN_EVENT, type OpenStepPlanDetail } from "./stepPlans";
import type { StepPlanItem } from "./types";

// Shell state the step-by-step section reads or drives. The once-registered
// listeners below keep the first render's deps, so they only touch refs and
// state setters.
export interface StepPlansControllerDeps {
  sidebarMode: SidebarMode;
  atHome: boolean;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  setAtHome: Dispatch<SetStateAction<boolean>>;
  closeWsRoute: () => void;
  closeMobileNav: () => void;
  confirmLeaveRecording: () => Promise<boolean>;
}

export interface StepPlansController {
  plans: StepPlanItem[];
  loading: boolean;
  error: string | null;
  activePlanId: number | null;
  /** The "new step plan from pasted text" dialog. */
  newDialogOpen: boolean;
  setNewDialogOpen: Dispatch<SetStateAction<boolean>>;
  reload: () => void;
  openPlan: (id: number | null) => Promise<void>;
  syncFromRoute: (r: AppRoute) => void;
  enterOverview: () => void;
  startNew: () => void;
}

export function useStepPlansController(deps: StepPlansControllerDeps): StepPlansController {
  const {
    sidebarMode,
    atHome,
    setSidebarMode,
    setAtHome,
    closeWsRoute,
    closeMobileNav,
    confirmLeaveRecording,
  } = deps;

  const [activePlanId, setActivePlanId] = useState<number | null>(() => parseAppRoute().stepPlanRef);
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  const [plans, setPlans] = useState<StepPlanItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const loadedRef = useRef(false);
  const active = sidebarMode === "steps" && !atHome;

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  // The list is only shown in this section, so it loads on entry and again on
  // every change another window (or a model action) publishes.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    if (!loadedRef.current) setLoading(true);
    api.stepPlans
      .list()
      .then((items) => {
        if (cancelled) return;
        setPlans(items);
        setError(null);
        loadedRef.current = true;
      })
      .catch((err) => {
        if (!cancelled) setError(apiErrorMessage(err, "Could not load step plans."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active, reloadKey]);

  // Leaving the section never revives a half-opened dialog.
  useEffect(() => {
    if (sidebarMode !== "steps") setNewDialogOpen(false);
  }, [sidebarMode]);

  useEffect(() => {
    eventBus.start();
    return eventBus.subscribe((event) => {
      if (event.type === "step-plan.changed") setReloadKey((k) => k + 1);
    });
  }, []);

  // activePlanId -> /steps/<id> (or /steps for the overview). A plan change is a
  // navigation, so Back returns to the previous plan or the overview.
  useEffect(() => {
    if (atHome || sidebarMode !== "steps") return;
    const target = stepPlanUrl(activePlanId);
    if (window.location.pathname !== target) navigate(target);
  }, [activePlanId, sidebarMode, atHome]);

  async function openPlan(id: number | null): Promise<void> {
    if (!(await confirmLeaveRecording())) return;
    setAtHome(false);
    closeWsRoute();
    setNewDialogOpen(false);
    setActivePlanId(id);
    setSidebarMode("steps");
    closeMobileNav();
    const target = stepPlanUrl(id);
    if (window.location.pathname !== target) navigate(target);
    reload();
  }

  // Another section (a topic's `/steps` command or button) asked to open a plan.
  const openPlanRef = useRef(openPlan);
  openPlanRef.current = openPlan;
  useEffect(() => {
    function onOpen(e: Event): void {
      const { planId } = (e as CustomEvent<OpenStepPlanDetail>).detail;
      void openPlanRef.current(planId);
    }
    window.addEventListener(OPEN_STEP_PLAN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_STEP_PLAN_EVENT, onOpen);
  }, []);

  function syncFromRoute(r: AppRoute): void {
    setActivePlanId(r.stepPlanRef);
    setNewDialogOpen(false);
  }

  // Section navigation always opens the overview.
  function enterOverview(): void {
    setActivePlanId(null);
    setNewDialogOpen(false);
  }

  // The sidebar "+" drops the selection and opens the new-plan dialog.
  function startNew(): void {
    setActivePlanId(null);
    setNewDialogOpen(true);
  }

  return {
    plans,
    loading,
    error,
    activePlanId,
    newDialogOpen,
    setNewDialogOpen,
    reload,
    openPlan,
    syncFromRoute,
    enterOverview,
    startNew,
  };
}
