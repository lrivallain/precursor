import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { navigate, searchTermFromUrl } from "./routes";
import type { Chat, Topic } from "./types";

// The selection state every section controller owns. The effects below re-fire
// on it, so this hook must be called after every controller: its `?q=` mirror
// has to run after their pathname URL effects in the same commit, or a
// navigation drops the query.
export interface SearchHighlightDeps {
  atHome: boolean;
  sidebarMode: SidebarMode;
  activeTopic: Topic | null;
  activeChat: Chat | null;
  activeAgentId: number | null;
  activeSessionId: number | null;
}

// Named apart from `useSearchHighlight` in `searchHighlight.tsx`, which reads
// the term this owns back out of the provider.
export interface SearchHighlightController {
  searchHighlight: string;
  setSearchHighlight: Dispatch<SetStateAction<string>>;
  // App's once-registered `syncFromUrl` holds the first render's copies, so
  // both only touch the setter and refs.
  syncFromUrl: () => void;
  openForSearch: (targetKey: string, term: string) => void;
}

export function useSearchHighlightController(
  deps: SearchHighlightDeps,
): SearchHighlightController {
  const { atHome, sidebarMode, activeTopic, activeChat, activeAgentId, activeSessionId } =
    deps;

  // Active "find" term for the open conversation, seeded from the ?q= URL param
  // and set when a content-search hit is opened. Highlights matches in message
  // bodies; empty means no highlighting.
  const [searchHighlight, setSearchHighlight] = useState<string>(searchTermFromUrl);
  // The conversation the current highlight belongs to (a `${mode}:${id}` key),
  // so we can auto-clear the highlight when the user navigates to a *different*
  // conversation. `pendingHighlightKeyRef` holds the target of an in-flight
  // search-open so the transition to it isn't mistaken for a navigation-away.
  const highlightKeyRef = useRef<string | null>(null);
  const pendingHighlightKeyRef = useRef<string | null>(null);

  // App's mount + back/forward URL sync.
  function syncFromUrl(): void {
    // Keep the highlight term in step with the URL for reloads / back-forward.
    // Reset the ownership refs so the highlight adopts whichever conversation
    // the URL resolves to (rather than clearing on that first resolution).
    const urlTerm = searchTermFromUrl();
    setSearchHighlight(urlTerm);
    if (urlTerm) {
      highlightKeyRef.current = null;
      pendingHighlightKeyRef.current = null;
    }
  }

  // App's content-search open.
  function openForSearch(targetKey: string, term: string): void {
    // Carry the matched term into the opened view so its bodies get highlighted;
    // the ?q= URL sync effect mirrors it for shareable/reloadable links. Record
    // the target conversation so the navigation-away auto-clear waits until we
    // actually land on it instead of clearing during the transition.
    pendingHighlightKeyRef.current = targetKey;
    highlightKeyRef.current = null;
    setSearchHighlight(term);
  }

  // Auto-clear the search highlight when the user navigates to a *different*
  // conversation than the one it was opened for. The highlight is tied to a
  // single conversation (`${mode}:${id}`): while a search-open is in flight we
  // wait until the selection lands on its target; a URL-loaded term adopts the
  // first conversation it resolves to; any later switch to a different one
  // clears it. Transitional states with no complete selection are ignored.
  useEffect(() => {
    if (!searchHighlight.trim()) return;
    let key: string | null = null;
    if (sidebarMode === "topics") key = activeTopic ? `topics:${activeTopic.id}` : null;
    else if (sidebarMode === "chats") key = activeChat ? `chats:${activeChat.id}` : null;
    else if (sidebarMode === "agents")
      key = activeAgentId != null ? `agents:${activeAgentId}` : null;
    else if (sidebarMode === "live")
      key = activeSessionId != null ? `live:${activeSessionId}` : null;
    if (key == null) return; // mid-transition — wait for a complete selection
    const pending = pendingHighlightKeyRef.current;
    if (pending != null) {
      // Still travelling to the just-opened search target; adopt once we arrive.
      if (key === pending) {
        highlightKeyRef.current = pending;
        pendingHighlightKeyRef.current = null;
      }
      return;
    }
    if (highlightKeyRef.current == null) {
      highlightKeyRef.current = key; // first resolved conversation owns the term
      return;
    }
    if (key !== highlightKeyRef.current) {
      setSearchHighlight("");
      highlightKeyRef.current = null;
    }
  }, [searchHighlight, sidebarMode, activeTopic, activeChat, activeAgentId, activeSessionId]);

  // Mirror the highlight term into `?q=` on the current path so a reloaded or
  // shared link re-highlights. Runs after the pathname effects above (which
  // pushState a path without a query), re-appending `q` via replaceState.
  // Depends on the selection state so it re-fires after each navigation. The
  // query is dropped at home, where there's no conversation to highlight.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const cur = params.get("q") ?? "";
    const want = atHome ? "" : searchHighlight.trim();
    if (cur === want) return;
    if (want) params.set("q", want);
    else params.delete("q");
    const qs = params.toString();
    navigate(window.location.pathname + (qs ? `?${qs}` : ""), { replace: true });
  }, [
    searchHighlight,
    atHome,
    sidebarMode,
    activeTopic,
    activeChat,
    activeAgentId,
    activeSessionId,
  ]);

  // Bring the first highlighted match into view once the opened conversation has
  // rendered. Messages load asynchronously, so poll briefly until a match
  // appears (or give up after ~2.5s).
  useEffect(() => {
    if (!searchHighlight.trim() || atHome) return;
    let tries = 0;
    const id = window.setInterval(() => {
      const el = document.querySelector(".search-hl");
      if (el) {
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        window.clearInterval(id);
      } else if (++tries > 20) {
        window.clearInterval(id);
      }
    }, 120);
    return () => window.clearInterval(id);
  }, [
    searchHighlight,
    atHome,
    activeTopic,
    activeChat,
    activeAgentId,
    activeSessionId,
  ]);

  return { searchHighlight, setSearchHighlight, syncFromUrl, openForSearch };
}
