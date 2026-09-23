import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { pluginSettingsTab } from "../components/SettingsPanel";
import type { SectionHost, SectionPlugin } from "./plugins";
import {
  isPluginMode,
  navigate,
  parseAppRoute,
  pluginSectionUrl,
  type AppRoute,
  type PluginRoute,
} from "./routes";
import type { PluginDescriptor, Settings } from "./types";

// What the plugin section host reads or drives. App's once-registered
// `syncFromUrl` keeps the first render's controller, so `syncFromRoute` only
// touches a state setter. The memoised host doesn't re-run on `openSettings`,
// so it must be stable.
export interface PluginSectionsDeps {
  sidebarMode: SidebarMode;
  sidebarModeRef: RefObject<SidebarMode>;
  settings: Settings | null;
  pluginDescriptors: PluginDescriptor[] | null;
  enabledSections: SectionPlugin[];
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  openSettings: (category: string) => void;
  changeMode: (next: SidebarMode) => Promise<void>;
  handleSelect: (topicId: number) => Promise<void>;
}

export interface PluginSectionsController {
  sectionHost: SectionHost;
  syncFromRoute: (r: AppRoute) => void;
  sectionUrl: (mode: SidebarMode) => string;
}

// Hosts the active plugin section: its opaque sub-route, the services core
// hands it, and the guard that bounces a section which went away, like the
// live-disabled one. Called after the `?q=` mirror, where App's bounce used to
// run.
export function usePluginSections(deps: PluginSectionsDeps): PluginSectionsController {
  const {
    sidebarMode,
    sidebarModeRef,
    settings,
    pluginDescriptors,
    enabledSections,
    setSidebarMode,
    openSettings,
    changeMode,
    handleSelect,
  } = deps;

  // Route state owned by the active plugin section (opaque to core).
  const [pluginRoute, setPluginRoute] = useState<PluginRoute>(() => {
    const r = parseAppRoute();
    return { segments: r.pluginSegments, hash: r.pluginHash };
  });

  // Same guard for plugin sections: a section whose backend package is gone —
  // or whose own `isEnabled` turned false (kanban loses its GitHub repo, say) —
  // can't stay open, and a deep link to it must fall back to Topics. Waits for
  // the descriptors *and* settings so a valid deep link isn't bounced before
  // they resolve.
  useEffect(() => {
    if (settings == null || pluginDescriptors == null) return;
    if (!isPluginMode(sidebarMode)) return;
    if (enabledSections.some((sec) => sec.id === sidebarMode)) return;
    // Any unrecognised root segment parses as a candidate plugin section, so
    // this also catches plain typos — all the more reason to replace rather
    // than push, or Back would bounce off the bad URL forever.
    navigate("/topics", { replace: true });
    setSidebarMode("topics");
  }, [enabledSections, pluginDescriptors, sidebarMode, settings]);

  // Mirror the section sub-route so changeMode can restore it without
  // re-subscribing to every route change.
  const pluginRouteRef = useRef(pluginRoute);
  useEffect(() => {
    pluginRouteRef.current = pluginRoute;
  }, [pluginRoute]);

  // `changeMode` and `handleSelect` are plain function declarations, so every
  // render makes new ones closing over that render's state. The host below is
  // memoised and would pin whichever pair it was built with — and `changeMode`
  // short-circuits on a stale `sidebarMode`, so a section's "open topic" would
  // silently do nothing. Read them through refs instead.
  const changeModeRef = useRef(changeMode);
  const handleSelectRef = useRef(handleSelect);
  useEffect(() => {
    changeModeRef.current = changeMode;
    handleSelectRef.current = handleSelect;
  });

  // The services a plugin section gets from core. Memoised on the values it
  // closes over so a section's effects don't re-run on unrelated app renders.
  const sectionHost = useMemo<SectionHost>(
    () => ({
      segments: pluginRoute.segments,
      hash: pluginRoute.hash,
      navigate: (segments, hash = "", opts) => {
        // Idempotent: a section re-asserting the URL it already has must not
        // spin the render loop that produced it.
        setPluginRoute((prev) =>
          prev.hash === hash &&
          prev.segments.length === segments.length &&
          prev.segments.every((seg, i) => seg === segments[i])
            ? prev
            : { segments, hash },
        );
        const path =
          pluginSectionUrl(sidebarModeRef.current, segments) +
          window.location.search +
          (hash ? `#${hash}` : "");
        if (window.location.pathname + window.location.search + window.location.hash === path) {
          return;
        }
        navigate(path, { replace: !opts?.push });
      },
      openTopic: (topicId: number) => {
        void changeModeRef.current("topics");
        void handleSelectRef.current(topicId);
      },
      openSettings: (pluginPageId?: string) => {
        openSettings(pluginPageId ? pluginSettingsTab(pluginPageId) : "plugins");
      },
      settings,
    }),
    [pluginRoute, settings],
  );

  // App's mount + back/forward URL sync: a plugin section owns everything under
  // its root, so hand it the fresh segments/hash and let it reconcile.
  function syncFromRoute(r: AppRoute): void {
    setPluginRoute({ segments: r.pluginSegments, hash: r.pluginHash });
  }

  // The URL that re-enters a plugin section at the sub-route it was left at.
  function sectionUrl(mode: SidebarMode): string {
    return pluginSectionUrl(mode, pluginRouteRef.current.segments);
  }

  return { sectionHost, syncFromRoute, sectionUrl };
}
