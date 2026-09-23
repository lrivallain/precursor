import { useEffect, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import type { useConfirm } from "../components/ConfirmDialog";
import { api } from "./api";
import { eventBus } from "./events";
import { liveUrl, navigate, parseAppRoute, type AppRoute } from "./routes";
import type { MeetingSession } from "./types";

// Shell state the live section reads or drives. The once-registered listeners
// (App's `syncFromUrl`, the event bus below) keep the first render's deps, so
// they only touch refs and state setters.
export interface LiveSessionsControllerDeps {
  sidebarMode: SidebarMode;
  atHome: boolean;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  setAtHome: Dispatch<SetStateAction<boolean>>;
  closeMobileNav: () => void;
  confirmAction: ReturnType<typeof useConfirm>;
}

export interface LiveSessionsController {
  meetingSessions: MeetingSession[] | null;
  setMeetingSessions: Dispatch<SetStateAction<MeetingSession[] | null>>;
  meetingSessionsRef: RefObject<MeetingSession[] | null>;
  activeSessionId: number | null;
  setActiveSessionId: Dispatch<SetStateAction<number | null>>;
  activeSessionIdRef: RefObject<number | null>;
  activeSession: MeetingSession | null;
  liveRecordingId: number | null;
  setLiveRecordingId: Dispatch<SetStateAction<number | null>>;
  loadMeetingSessions: () => Promise<MeetingSession[]>;
  handleSelectSession: (session: MeetingSession) => Promise<void>;
  handleRenameSession: (session: MeetingSession, title: string) => Promise<void>;
  handleArchiveSessions: (ids: number[]) => Promise<void>;
  confirmLeaveRecording: () => Promise<boolean>;
  createLiveFromHome: (session: MeetingSession) => Promise<void>;
  syncFromRoute: (r: AppRoute) => void;
  enterOverview: () => void;
  startNew: () => void;
  openSearchHit: (sessionId: number) => Promise<void>;
}

export interface LiveSessionsLateEffectsDeps {
  sidebarMode: SidebarMode;
  liveEnabled: boolean;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
}

export function useLiveSessionsController(
  deps: LiveSessionsControllerDeps,
): LiveSessionsController {
  const {
    sidebarMode,
    atHome,
    setSidebarMode,
    setAtHome,
    closeMobileNav,
    confirmAction,
  } = deps;

  // Live meeting sessions are loaded lazily when the user first enters live mode.
  const [meetingSessions, setMeetingSessions] = useState<MeetingSession[] | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [liveRecordingId, setLiveRecordingId] = useState<number | null>(null);

  // Mirror the active meeting session into refs so changeMode / URL sync can
  // build the /live URL without re-subscribing.
  const meetingSessionsRef = useRef<MeetingSession[] | null>(meetingSessions);
  useEffect(() => {
    meetingSessionsRef.current = meetingSessions;
  }, [meetingSessions]);
  const activeSessionIdRef = useRef<number | null>(activeSessionId);
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  // activeSession -> /live/<slug> (or /live when nothing is selected).
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "live") return;
    // Until the list loads, a deep-linked slug can't be resolved and looks like
    // "nothing selected". Writing `/live` now would push a transient entry that
    // the resolved slug then pushes over. Every surface that enters Live before
    // the list has loaded writes its own URL.
    if (meetingSessions === null) return;
    const active = meetingSessions.find((s) => s.id === activeSessionId) ?? null;
    const target = liveUrl(active);
    if (window.location.pathname === target) return;
    // A slug naming no session (archived, deleted or mistyped) is a dead entry:
    // normalise it in place. Pushing would add an entry on a cold load, and on
    // Back it would wipe the forward history every time, trapping the user.
    const slug = parseAppRoute().liveSlug;
    navigate(target, { replace: slug != null && !meetingSessions.some((s) => s.slug === slug) });
  }, [activeSessionId, meetingSessions, sidebarMode, atHome]);

  // While a live session is recording, confirm before any in-app navigation
  // that would unmount the LiveView and stop the capture. Resolves immediately
  // when nothing is recording, so guarded handlers are unchanged off the happy
  // path. Page-unload (reload/close/quit) is guarded separately in LiveView via
  // a native beforeunload prompt.
  async function confirmLeaveRecording(): Promise<boolean> {
    if (liveRecordingId == null) return true;
    return confirmAction({
      title: "Recording in progress",
      message:
        "You're recording a live session. Leaving this screen stops the recording. Leave anyway?",
      confirmLabel: "Leave & stop recording",
      cancelLabel: "Keep recording",
      variant: "warning",
    });
  }

  // The "New live session" card's inline form: create, then reveal the session.
  async function createLiveFromHome(session: MeetingSession): Promise<void> {
    setAtHome(false);
    setSidebarMode("live");
    await loadMeetingSessions();
    setActiveSessionId(session.id);
    navigate(liveUrl(session));
  }

  // Live sync across windows: meeting changes arrive over the shared event bus.
  // `start()` is idempotent, so every controller starting it is harmless.
  useEffect(() => {
    eventBus.start();
    const off = eventBus.subscribe((event) => {
      if (event.type === "meeting.changed") {
        // A meeting session was created, renamed, ended, or deleted (possibly in
        // another tab). Refresh the list if we've loaded it so the Live section
        // stays current.
        if (meetingSessionsRef.current !== null) void loadMeetingSessions();
      }
    });
    return () => {
      off();
    };
  }, []);

  const activeSession =
    meetingSessions?.find((s) => s.id === activeSessionId) ?? null;

  async function loadMeetingSessions(): Promise<MeetingSession[]> {
    const list = await api.meetings.listSessions();
    setMeetingSessions(list);
    return list;
  }

  async function handleSelectSession(session: MeetingSession): Promise<void> {
    // Switching to a different session unmounts the recording LiveView; confirm
    // first so an accidental click doesn't drop an in-progress capture.
    if (session.id !== activeSessionId && !(await confirmLeaveRecording())) return;
    closeMobileNav();
    setActiveSessionId(session.id);
    navigate(liveUrl(session));
  }

  async function handleRenameSession(session: MeetingSession, title: string): Promise<void> {
    const updated = await api.meetings.updateSession(session.id, { title });
    setMeetingSessions((prev) =>
      prev ? prev.map((s) => (s.id === updated.id ? updated : s)) : prev,
    );
  }

  async function handleArchiveSessions(ids: number[]): Promise<void> {
    await Promise.all(ids.map((id) => api.meetings.archiveSession(id)));
    if (activeSessionId != null && ids.includes(activeSessionId)) setActiveSessionId(null);
    await loadMeetingSessions();
  }

  // The live branch of App's mount + back/forward URL sync.
  function syncFromRoute(r: AppRoute): void {
    const slug = r.liveSlug;
    if (!slug) {
      setActiveSessionId(null);
      return;
    }
    const existing = meetingSessionsRef.current?.find((s) => s.slug === slug);
    if (existing) {
      setActiveSessionId(existing.id);
      return;
    }
    void loadMeetingSessions().then((list) => {
      const found = list.find((s) => s.slug === slug);
      setActiveSessionId(found ? found.id : null);
    });
  }

  function enterOverview(): void {
    // Entering the Live section lands on the create surface (like starting a
    // fresh topic/agent): drop the selection so the "new session" hero shows.
    // Existing sessions stay one click away in the list.
    setActiveSessionId(null);
  }

  // Drop the selection to reveal the "new session" start hero.
  function startNew(): void {
    setActiveSessionId(null);
  }

  // The live branch of App's content-search open.
  async function openSearchHit(sessionId: number): Promise<void> {
    // Ensure the session list is loaded so the URL-sync effect can resolve
    // the slug once we select it.
    if (!meetingSessionsRef.current?.some((s) => s.id === sessionId)) {
      await loadMeetingSessions();
    }
    setActiveSessionId(sessionId);
  }

  return {
    meetingSessions,
    setMeetingSessions,
    meetingSessionsRef,
    activeSessionId,
    setActiveSessionId,
    activeSessionIdRef,
    activeSession,
    liveRecordingId,
    setLiveRecordingId,
    loadMeetingSessions,
    handleSelectSession,
    handleRenameSession,
    handleArchiveSessions,
    confirmLeaveRecording,
    createLiveFromHome,
    syncFromRoute,
    enterOverview,
    startNew,
    openSearchHit,
  };
}

// The live section's effects that must run after App's `?q=` mirror, which is
// where they ran before the section moved out of App. In a commit that both
// enters Live and bounces (Back onto a /live URL while Live is disabled), a
// mirror running after the bounce would re-append `?q=` to the consumed entry.
// The lazy load stays after the bounce so it reads the URL the bounce left.
export function useLiveSessionsLateEffects(
  controller: LiveSessionsController,
  deps: LiveSessionsLateEffectsDeps,
): void {
  const { sidebarMode, liveEnabled, setSidebarMode } = deps;
  const { meetingSessions, loadMeetingSessions, setActiveSessionId } = controller;

  // If the Live section gets disabled while it's open (or a deep link lands on
  // it while disabled), fall back to Topics.
  useEffect(() => {
    if (!liveEnabled && sidebarMode === "live") {
      // replaceState, not push: this bounce must *consume* the unreachable URL.
      // Pushing would leave it in the history, and going Back would land on it
      // and bounce again — trapping the user one entry from where they were.
      navigate("/topics", { replace: true });
      setSidebarMode("topics");
    }
  }, [liveEnabled, sidebarMode]);

  // Lazily load sessions the first time the user enters live mode, then pick an
  // active one (honouring a slug from the URL, else the first).
  useEffect(() => {
    if (sidebarMode !== "live" || meetingSessions !== null) return;
    const { liveSlug } = parseAppRoute();
    void loadMeetingSessions().then((list) => {
      if (list.length === 0) return;
      const fromRoute = liveSlug ? list.find((s) => s.slug === liveSlug) : undefined;
      // Only auto-select when the URL points at a specific session; otherwise
      // leave nothing selected so the start hero shows.
      if (fromRoute) setActiveSessionId((id) => id ?? fromRoute.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarMode]);
}
