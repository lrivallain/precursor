import { useCallback, useEffect, useState } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { browseOpensDrawer } from "./sections";

/**
 * The phone layout's off-canvas sidebar drawer. Its state is meaningless when
 * the sidebar is in-flow, so growing past the breakpoint drops it and returning
 * to a narrow viewport doesn't reopen it.
 */
export function useMobileNav(narrow: boolean) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const closeMobileNav = useCallback(() => setMobileNavOpen(false), []);
  useEffect(() => {
    if (!narrow) setMobileNavOpen(false);
  }, [narrow]);
  // The home launcher's "Browse …": on a phone the list is in the closed
  // drawer, so open it instead of leaving the user on an empty "new" pane.
  const openNavToBrowse = useCallback(
    (mode: SidebarMode) => {
      if (narrow && browseOpensDrawer(mode)) setMobileNavOpen(true);
    },
    [narrow],
  );
  return { mobileNavOpen, setMobileNavOpen, closeMobileNav, openNavToBrowse };
}
