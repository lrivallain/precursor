import { useEffect, useState } from "react";
import { api } from "./api";

let labels: Record<string, string> | null = null;
let pending: Promise<void> | null = null;
const listeners = new Set<() => void>();

function load(): void {
  if (labels || pending) return;
  pending = api.llm
    .listProviders()
    .then((list) => {
      labels = Object.fromEntries(list.map((entry) => [entry.id, entry.label]));
      listeners.forEach((notify) => notify());
    })
    .catch(() => {})
    .finally(() => { pending = null; });
}

/** Human label for a provider id; falls back to the id until the list loads. */
export function useProviderLabel(id: string | null | undefined): string | null {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!id) return;
    const notify = () => bump((n) => n + 1);
    listeners.add(notify);
    load();
    return () => { listeners.delete(notify); };
  }, [id]);
  if (!id) return null;
  return labels?.[id] ?? id;
}
