import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { hasUnsavedEdits } from "./stepPlans";

/**
 * Unsaved text typed into the step-by-step editors (a step's artifact, the
 * final output). It lives outside the components so it survives moving between
 * steps, the final output, other plans and other sections within the session;
 * an entry exists only while it differs from what is saved. Nothing is
 * persisted: a page reload asks first (see `useUnsavedGuard`).
 */
interface DraftEntry {
  draft: string;
  base: string;
}

const entries = new Map<string, DraftEntry>();
const listeners = new Set<() => void>();
let version = 0;

function emit(): void {
  version += 1;
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** `<planId>/final` or `<planId>/step/<stepId>`. */
export function draftKey(planId: number, target: "final" | number): string {
  return target === "final" ? `${planId}/final` : `${planId}/step/${target}`;
}

export function getDraft(key: string): DraftEntry | undefined {
  return entries.get(key);
}

export function putDraft(key: string, draft: string, base: string): void {
  const existing = entries.get(key);
  if (!hasUnsavedEdits(draft, base)) {
    if (existing) {
      entries.delete(key);
      emit();
    }
    return;
  }
  if (existing && existing.draft === draft && existing.base === base) return;
  entries.set(key, { draft, base });
  emit();
}

export function dropDraft(key: string): void {
  if (entries.delete(key)) emit();
}

export function dropPlanDrafts(planId: number): void {
  let changed = false;
  for (const key of [...entries.keys()]) {
    if (key.startsWith(`${planId}/`)) {
      entries.delete(key);
      changed = true;
    }
  }
  if (changed) emit();
}

/** The keys with unsaved edits in `planId`, for the "unsaved" markers. */
export function useUnsavedKeys(planId: number): ReadonlySet<string> {
  const seen = useSyncExternalStore(subscribe, () => version);
  // `seen` is the cache key: the set is rebuilt only when a draft changed.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => new Set([...entries.keys()].filter((k) => k.startsWith(`${planId}/`))), [seen, planId]);
}

/**
 * A draft that outlives its component: starts from the stored unsaved text
 * (when there is one) instead of `saved`, and mirrors every change back.
 */
export function useStoredDraft(key: string, saved: string) {
  const [draft, setDraft] = useState(() => getDraft(key)?.draft ?? saved);
  const [base, setBase] = useState(() => getDraft(key)?.base ?? saved);
  useEffect(() => {
    putDraft(key, draft, base);
  }, [key, draft, base]);
  return { draft, setDraft, base, setBase };
}

/** Ask the browser to confirm leaving (reload, close) while any edit is unsaved. */
export function useUnsavedGuard(): void {
  const handler = useCallback((e: BeforeUnloadEvent) => {
    if (entries.size === 0) return;
    e.preventDefault();
    e.returnValue = "";
  }, []);
  useEffect(() => {
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [handler]);
}
