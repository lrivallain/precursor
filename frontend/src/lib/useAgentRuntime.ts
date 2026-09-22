import { useCallback, useEffect, useSyncExternalStore } from "react";
import { api } from "./api";
import { settingsStore } from "./settingsStore";
import type { AgentRuntimeStatus } from "./types";

const RUNTIME_POLL_MS = 1500;

interface RuntimeSnapshot {
  runtime: AgentRuntimeStatus | null;
  loading: boolean;
  error: string | null;
}

// Settings can open over an Agents view. Both must observe the same job so
// closing the modal neither loses its progress nor leaves two polling loops.
class AgentRuntimeStore {
  private snapshot: RuntimeSnapshot = { runtime: null, loading: false, error: null };
  private listeners = new Set<() => void>();
  private requestId = 0;
  private request: Promise<void> | null = null;
  private timer: number | null = null;
  private syncedJob: number | null = null;

  getSnapshot = (): RuntimeSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0) {
        this.clearTimer();
        this.requestId++;
        this.request = null;
        this.snapshot = { ...this.snapshot, loading: false };
      }
    };
  };

  private set(update: Partial<RuntimeSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...update };
    for (const listener of this.listeners) listener();
  }

  private clearTimer(): void {
    if (this.timer != null) window.clearTimeout(this.timer);
    this.timer = null;
  }

  private async read(id: number, status?: AgentRuntimeStatus): Promise<void> {
    let syncing = false;
    try {
      const next = await (status ?? api.agents.getRuntime());
      if (id !== this.requestId) return;
      this.set({ runtime: next });
      const settings = settingsStore.current();
      const completedJob = next.job?.state === "succeeded" ? next.job.started_at : null;
      // Also reconcile a job that finished while this surface was closed, or a
      // CLI installed elsewhere since the app last fetched settings.
      if (
        next.job?.state !== "running" &&
        ((completedJob != null && completedJob !== this.syncedJob) ||
          settings == null ||
          settings.agents_available !== next.available ||
          settings.agents_runtime_started !== next.runtime_started)
      ) {
        syncing = true;
        const updated = await api.settings.get();
        if (id !== this.requestId) return;
        this.syncedJob = completedJob;
        settingsStore.set(updated);
      }
    } catch (e) {
      if (id === this.requestId) {
        const detail = e instanceof Error ? e.message : String(e);
        const message = syncing
          ? "Could not refresh Agents settings"
          : "Could not load the Copilot runtime status";
        this.set({ error: `${message}: ${detail}` });
      }
    } finally {
      if (id === this.requestId) {
        this.request = null;
        this.set({ loading: false });
        if (
          this.listeners.size > 0 &&
          !this.snapshot.error &&
          this.snapshot.runtime?.job?.state === "running"
        ) {
          this.timer = window.setTimeout(() => void this.refresh(), RUNTIME_POLL_MS);
        }
      }
    }
  }

  update = (status?: AgentRuntimeStatus): Promise<void> => {
    if (this.listeners.size === 0) return Promise.resolve();
    if (status === undefined && this.request) return this.request;
    this.clearTimer();
    const id = ++this.requestId;
    this.set({ loading: true, error: null });
    this.request = this.read(id, status);
    return this.request;
  };

  refresh = (): Promise<void> => this.update();
}

const runtimeStore = new AgentRuntimeStore();

export function useAgentRuntime(active = true) {
  const subscribe = useCallback(
    (listener: () => void) => active ? runtimeStore.subscribe(listener) : () => {},
    [active],
  );
  const snapshot = useSyncExternalStore(subscribe, runtimeStore.getSnapshot, runtimeStore.getSnapshot);
  useEffect(() => {
    if (active) void runtimeStore.refresh();
  }, [active]);

  return { ...snapshot, refresh: runtimeStore.refresh, update: runtimeStore.update };
}
