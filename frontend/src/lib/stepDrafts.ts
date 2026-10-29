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
// Fields whose last autosave failed: their text is kept and a retry is offered.
const failed = new Set<string>();
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
  const hadFailure = failed.delete(key);
  if (entries.delete(key) || hadFailure) emit();
}

export function dropPlanDrafts(planId: number): void {
  let changed = false;
  for (const key of [...entries.keys(), ...failed]) {
    if (key.startsWith(`${planId}/`) && failed.delete(key)) changed = true;
    if (key.startsWith(`${planId}/`)) {
      entries.delete(key);
      changed = true;
    }
  }
  if (changed) emit();
}

/**
 * `<planId>/step/<stepId>/title|instruction`, `<planId>/instructions` (the
 * output instructions) or `<planId>/ask`: the autosaved text fields.
 */
export type FieldName = "title" | "instruction";
export function stepFieldKey(planId: number, stepId: number, field: FieldName): string {
  return `${planId}/step/${stepId}/${field}`;
}
export function planFieldKey(planId: number, field: "instructions" | "ask"): string {
  return `${planId}/${field}`;
}

/** Whether any text of a step (its artifact, title or instruction) is unsaved. */
export function stepHasUnsaved(unsaved: ReadonlySet<string>, planId: number, stepId: number): boolean {
  return (
    unsaved.has(draftKey(planId, stepId)) ||
    unsaved.has(stepFieldKey(planId, stepId, "title")) ||
    unsaved.has(stepFieldKey(planId, stepId, "instruction"))
  );
}

export function markFailed(keys: string[], isFailed: boolean): void {
  let changed = false;
  for (const key of keys) {
    if (isFailed && entries.has(key) && !failed.has(key)) {
      failed.add(key);
      changed = true;
    } else if (!isFailed && failed.delete(key)) changed = true;
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

/**
 * An autosaved text field (saved on blur) whose typed text outlives the
 * component. `saved` is the server's value; the text differs from it (ignoring
 * surrounding whitespace unless `exact`) only while it's unsaved. A failed
 * save leaves it that way, so nothing typed is lost: see `markFailed`.
 */
export function useFieldDraft(key: string, saved: string, exact = false) {
  const same = (a: string, b: string) => (exact ? a === b : a.trim() === b);
  const [value, setValue] = useState(() => getDraft(key)?.draft ?? saved);
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) {
    setSeenKey(key);
    setValue(getDraft(key)?.draft ?? saved);
  }
  // Follow the server unless there's text of the user's to protect.
  useEffect(() => {
    if (!getDraft(key)) setValue(saved);
  }, [key, saved]);
  useEffect(() => {
    if (same(value, saved)) {
      if (entries.delete(key) || failed.delete(key)) emit();
    } else if (getDraft(key)?.draft !== value || getDraft(key)?.base !== saved) {
      entries.set(key, { draft: value, base: saved });
      emit();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, value, saved, exact]);
  const seen = useSyncExternalStore(subscribe, () => version);
  const isFailed = useMemo(() => failed.has(key), [seen, key]); // eslint-disable-line react-hooks/exhaustive-deps
  return { value, setValue, failed: isFailed };
}
