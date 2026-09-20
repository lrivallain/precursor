import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type { TopicSummary } from "./types";

const AUTOSAVE_DELAY = 1200;

interface StoredDraft {
  content: string;
  base: Pick<TopicSummary, "content" | "revision">;
}

function restoreDraft(key: string): StoredDraft | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? "null") as StoredDraft | null;
    return value && typeof value.content === "string"
      && typeof value.base?.content === "string" && typeof value.base?.revision === "string"
      ? value : null;
  } catch {
    return null;
  }
}

export function useTopicSummaryDraft(
  topicId: number,
  summary: TopicSummary | null,
  save: (content: string, revision: string) => Promise<TopicSummary | null>,
) {
  const [, render] = useReducer((n: number) => n + 1, 0);
  const key = `precursor:topic-summary-draft:${topicId}`;
  const restored = useMemo(() => restoreDraft(key), [key]);
  const state = useRef({
    content: restored?.content ?? summary?.content ?? "",
    base: restored?.base ?? summary,
    failed: restored !== null,
    active: true,
    timer: null as ReturnType<typeof setTimeout> | null,
    pending: null as Promise<boolean> | null,
  });
  const saveRef = useRef(save);
  saveRef.current = save;
  const saving = state.current.pending !== null;
  const dirty = state.current.failed || state.current.content !== (state.current.base?.content ?? "");

  const notify = useCallback(() => {
    if (state.current.active) render();
  }, []);

  const clearTimer = useCallback(() => {
    if (state.current.timer !== null) clearTimeout(state.current.timer);
    state.current.timer = null;
  }, []);

  const remember = useCallback(() => {
    const s = state.current;
    try {
      if (s.base && (s.pending || s.failed || s.content !== s.base.content)) {
        sessionStorage.setItem(key, JSON.stringify({
          content: s.content, base: { content: s.base.content, revision: s.base.revision },
        }));
      } else {
        sessionStorage.removeItem(key);
      }
    } catch {
      // Storage can be unavailable; the in-memory draft and unload warning remain.
    }
  }, [key]);

  // Never advance a dirty draft to an external revision: its next save must
  // conflict, not silently overwrite a change made in another window.
  useEffect(() => {
    const s = state.current;
    if (s.pending) return;
    if (s.failed ? !summary || summary.content !== s.content : s.content !== (s.base?.content ?? "")) return;
    s.base = summary;
    s.content = summary?.content ?? "";
    s.failed = false;
    remember();
    notify();
  }, [summary, saving, dirty, notify, remember]);

  const flush = useCallback(async (drain = true): Promise<boolean> => {
    clearTimer();
    const s = state.current;
    if (s.pending) {
      if (!await s.pending) return false;
      return flush(drain);
    }
    if (!s.base || (!s.failed && s.content === s.base.content)) return true;
    const content = s.content;
    const revision = s.base.revision;
    s.failed = false;
    s.pending = (async () => {
      try {
        const saved = await saveRef.current(content, revision);
        if (!saved) {
          s.failed = true;
          return false;
        }
        s.base = saved;
        return true;
      } catch {
        s.failed = true;
        return false;
      } finally {
        s.pending = null;
        remember();
        notify();
      }
    })();
    notify();
    const ok = await s.pending;
    // Done, blur and unmount flush the latest text, including keystrokes made
    // during a slow save. Ordinary autosave leaves the next debounce in charge.
    return ok && drain ? flush() : ok;
  }, [clearTimer, notify, remember]);

  const change = useCallback((content: string) => {
    const s = state.current;
    s.content = content;
    s.failed = false;
    remember();
    clearTimer();
    if (s.base && (s.pending || content !== s.base.content)) {
      s.timer = setTimeout(() => void flush(false), AUTOSAVE_DELAY);
    }
    notify();
  }, [clearTimer, flush, notify, remember]);

  const discard = useCallback(() => {
    if (state.current.pending) return;
    clearTimer();
    state.current.base = summary;
    state.current.content = summary?.content ?? "";
    state.current.failed = false;
    remember();
    notify();
  }, [clearTimer, notify, summary, remember]);

  useEffect(() => {
    state.current.active = true;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      const s = state.current;
      if (!s.pending && !s.failed && s.content === (s.base?.content ?? "")) return;
      void flush();
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      state.current.active = false;
      window.removeEventListener("beforeunload", beforeUnload);
      clearTimer();
      if (!state.current.failed) void flush();
    };
  }, [clearTimer, flush]);

  return {
    content: state.current.content,
    dirty,
    saving,
    failed: state.current.failed,
    change,
    flush,
    discard,
  };
}
