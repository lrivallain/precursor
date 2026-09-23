import { useCallback, useEffect, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { api } from "./api";
import { navigate, parseWsRoute, workspaceUrl, type WsRoute } from "./routes";
import type { Workspace } from "./types";
import { subscribeOpenWorkspaceFile, workspaceFileUrl } from "./workspaceLink";

// Shell state the workspaces section reads or drives. App's once-registered
// `syncFromUrl` and the other sections' once-registered listeners keep the
// first render's `closeRoute` / `syncFromRoute`, so those only touch state
// setters.
export interface WorkspacesControllerDeps {
  sidebarMode: SidebarMode;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  closeMobileNav: () => void;
}

export interface WorkspacesController {
  workspaces: Workspace[] | null;
  activeWorkspaceId: number | null;
  setActiveWorkspaceId: Dispatch<SetStateAction<number | null>>;
  wsRoute: WsRoute;
  createWorkspaceOpen: boolean;
  setCreateWorkspaceOpen: Dispatch<SetStateAction<boolean>>;
  activeWorkspace: Workspace | null;
  workspaceInitialPath: string | null;
  loadWorkspaces: () => Promise<Workspace[]>;
  handleSelectWorkspace: (ws: Workspace) => void;
  navigateWorkspace: (slug: string | null, filePath: string | null) => void;
  // Stable: once-registered listeners (App's `syncFromUrl`, the agents
  // controller's `precursor:open-agent`) hold the first render's copy.
  closeRoute: () => void;
  syncFromRoute: () => void;
  startNew: () => void;
  setRoleForActive: (roleId: number | null) => Promise<void>;
}

export function useWorkspacesController(
  deps: WorkspacesControllerDeps,
): WorkspacesController {
  const { sidebarMode, setSidebarMode, closeMobileNav } = deps;

  const [wsRoute, setWsRoute] = useState<WsRoute>(parseWsRoute);
  // Workspaces are loaded lazily when the user first enters workspaces mode.
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<number | null>(null);
  const [createWorkspaceOpen, setCreateWorkspaceOpen] = useState(false);

  // A fresh object per call, as each inline reset used to build: sharing one
  // constant would let React bail out of re-renders these used to trigger.
  const closeRoute = useCallback((): void => {
    setWsRoute({ open: false, slug: null, path: null });
  }, []);

  // Reflect the active workspace + open file in the URL so a reload returns to
  // the same place. replaceState keeps it as a single history entry.
  function navigateWorkspace(slug: string | null, filePath: string | null): void {
    navigate(workspaceUrl(slug, filePath), { replace: true });
    setWsRoute({ open: true, slug, path: filePath });
  }

  const activeWorkspace =
    workspaces?.find((w) => w.id === activeWorkspaceId) ?? null;
  // The route path only applies to the workspace named in the URL.
  const workspaceInitialPath =
    activeWorkspace && activeWorkspace.slug === wsRoute.slug ? wsRoute.path : null;

  async function loadWorkspaces(): Promise<Workspace[]> {
    const list = await api.workspaces.list();
    setWorkspaces(list);
    return list;
  }

  // Lazily load workspaces the first time the user enters that mode, then pick
  // an active one (honouring a slug from the URL, else the first).
  useEffect(() => {
    if (sidebarMode !== "workspaces" || workspaces !== null) return;
    void loadWorkspaces().then((list) => {
      if (list.length === 0) return;
      const fromRoute = wsRoute.slug
        ? list.find((w) => w.slug === wsRoute.slug)
        : undefined;
      setActiveWorkspaceId((id) => id ?? fromRoute?.id ?? list[0].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarMode]);

  function handleSelectWorkspace(ws: Workspace): void {
    closeMobileNav();
    setActiveWorkspaceId(ws.id);
    navigateWorkspace(ws.slug, null);
  }

  // A workspace-file chip on a tool call (see lib/workspaceLink.ts) jumps from
  // the conversation straight to the file the assistant just wrote. The slug is
  // resolved to a workspace id here because the lazy loader above only consults
  // the URL on a cold start — by the time a chip is clicked the list is usually
  // loaded and an active workspace already picked. pushState (not replace) so
  // Back returns to the discussion.
  useEffect(() => {
    return subscribeOpenWorkspaceFile(({ slug, path }) => {
      void (async () => {
        const url = workspaceFileUrl(slug, path);
        if (url === null) return;
        const list = workspaces ?? (await loadWorkspaces());
        const target = list.find((w) => w.slug === slug);
        if (!target) return;
        setActiveWorkspaceId(target.id);
        navigate(url);
        setWsRoute({ open: true, slug, path });
        setSidebarMode("workspaces");
      })();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaces]);

  // Adopt the workspace route from the current URL: App's mount + back/forward
  // sync, and entering the section once App has navigated to `/ws`.
  function syncFromRoute(): void {
    setWsRoute(parseWsRoute());
  }

  // The sidebar "+" in workspaces mode opens the create dialog.
  function startNew(): void {
    setCreateWorkspaceOpen(true);
  }

  // The workspaces branch of App's shared role persistence (`setRoleForActive`).
  async function setRoleForActive(roleId: number | null): Promise<void> {
    if (!activeWorkspace) return;
    const updated = await api.workspaces.update(activeWorkspace.id, {
      role_id: roleId,
    });
    setWorkspaces((prev) =>
      prev ? prev.map((w) => (w.id === updated.id ? updated : w)) : prev,
    );
  }

  return {
    workspaces,
    activeWorkspaceId,
    setActiveWorkspaceId,
    wsRoute,
    createWorkspaceOpen,
    setCreateWorkspaceOpen,
    activeWorkspace,
    workspaceInitialPath,
    loadWorkspaces,
    handleSelectWorkspace,
    navigateWorkspace,
    closeRoute,
    syncFromRoute,
    startNew,
    setRoleForActive,
  };
}
