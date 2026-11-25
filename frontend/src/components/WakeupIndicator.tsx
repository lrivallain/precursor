import { useEffect, useState } from "react";
import { AlarmClockOff, PauseCircle, Play } from "lucide-react";
import { useBusyAction } from "../lib/useBusyAction";
import { useWakeup, wakeupCountdown, wakeupHint, wakeupStore, type WakeupKind } from "../lib/wakeupStore";

interface Target {
  kind: WakeupKind;
  id: number;
}

/** Orange pause icon on a sidebar row whose conversation a model paused. */
export function WakeupIcon({ kind, id, size = 12 }: Target & { size?: number }) {
  const wakeup = useWakeup(kind, id);
  if (!wakeup) return null;
  return (
    <span
      className="shrink-0 text-amber-500"
      role="img"
      aria-label="Paused, waiting to wake up"
      data-tooltip={wakeupHint(wakeup)}
    >
      <PauseCircle size={size} />
    </span>
  );
}

/** Re-render every `ms` so a countdown stays current. */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(handle);
  }, [ms]);
  return now;
}

/** A paused conversation's banner: when it wakes up, why, and wake now / cancel. */
export function WakeupBanner({ kind, id }: Target) {
  const wakeup = useWakeup(kind, id);
  const now = useNow(15_000);
  const action = useBusyAction();
  if (!wakeup) return null;
  return (
    <div
      role="status"
      className="flex items-center gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs"
    >
      <PauseCircle size={14} className="shrink-0 text-amber-500" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate" data-tooltip={`Wake-up prompt:\n${wakeup.prompt}`}>
        Paused · wakes up {wakeupCountdown(wakeup.wake_at, now)}
        {wakeup.reason ? ` · ${wakeup.reason}` : ""}
      </span>
      {action.error && <span className="shrink-0 text-red-500">{action.error}</span>}
      <button
        type="button"
        disabled={action.busy}
        onClick={() => void action.run(() => wakeupStore.wakeNow(wakeup))}
        className="flex shrink-0 items-center gap-1 text-accent hover:underline disabled:opacity-50"
        data-tooltip="Wake the conversation up now"
      >
        <Play size={12} aria-hidden="true" /> Wake now
      </button>
      <button
        type="button"
        disabled={action.busy}
        onClick={() => void action.run(() => wakeupStore.cancel(wakeup))}
        className="flex shrink-0 items-center gap-1 text-muted hover:text-text disabled:opacity-50"
        data-tooltip="Cancel the wake-up: the conversation stays as it is"
      >
        <AlarmClockOff size={12} aria-hidden="true" /> Cancel
      </button>
    </div>
  );
}
