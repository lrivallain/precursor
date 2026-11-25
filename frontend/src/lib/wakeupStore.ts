import { call } from "./apiClient";
import { eventBus } from "./events";
import { ListResourceStore, useStoreVersion } from "./externalStore";
import type * as Api from "./generated/apiTypes";

/** A conversation a model paused until it wakes itself up. */
export type Wakeup = Api.WakeupRead;
export type WakeupKind = Wakeup["kind"];

class WakeupStore extends ListResourceStore<Wakeup> {
  protected readonly label = "wakeups";
  private listening = false;

  protected fetchAll(): Promise<Wakeup[]> {
    return call("GET /api/wakeups");
  }

  /** Load once and follow `wakeup.changed` from then on. */
  start(): void {
    if (this.listening) return;
    this.listening = true;
    eventBus.subscribe((ev) => {
      if (ev.type === "wakeup.changed") void this.load();
    });
    void this.ensureLoaded();
  }

  find(kind: WakeupKind, id: number): Wakeup | undefined {
    return this.items.find((w) => w.kind === kind && w.target_id === id);
  }

  async wakeNow(wakeup: Wakeup): Promise<void> {
    await call("POST /api/wakeups/{wakeup_id}/wake", { path: { wakeup_id: wakeup.id } });
    await this.load();
  }

  async cancel(wakeup: Wakeup): Promise<void> {
    await call("DELETE /api/wakeups/{wakeup_id}", { path: { wakeup_id: wakeup.id } });
    await this.load();
  }
}

export const wakeupStore = new WakeupStore();

/** The pending wakeup of a topic, chat or agent, if it is paused. */
export function useWakeup(kind: WakeupKind, id: number): Wakeup | undefined {
  wakeupStore.start();
  useStoreVersion(wakeupStore);
  return wakeupStore.find(kind, id);
}

/** "in 12 min", "in 3 h", "in 2 days", or "now" for a due wakeup. */
export function wakeupCountdown(wakeAt: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(wakeAt).getTime() - now) / 1000);
  if (seconds <= 0) return "now";
  if (seconds < 60) return `in ${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `in ${hours} h`;
  return `in ${Math.round(hours / 24)} days`;
}

/** The hover hint of a paused conversation. */
export function wakeupHint(wakeup: Wakeup, now: number = Date.now()): string {
  const at = new Date(wakeup.wake_at).toLocaleString();
  const head = `Paused: wakes up ${wakeupCountdown(wakeup.wake_at, now)} (${at})`;
  return wakeup.reason ? `${head}\n${wakeup.reason}` : head;
}
