import type { ReactNode } from "react";
import { Bot, Settings as SettingsIcon } from "lucide-react";

// The runtime is missing or not started: get it ready here without leaving
// Precursor.
export function AgentRuntimeSetup({
  available,
  runtimeCard,
  onOpenSettings,
}: {
  available: boolean;
  runtimeCard: ReactNode;
  onOpenSettings: () => void;
}) {
  return (
    <div className="flex h-full overflow-y-auto">
      <div className="m-auto w-full max-w-xl space-y-4 p-6 sm:p-8">
        <div className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <Bot size={18} /> {available ? "Recover Agents" : "Set up Agents"}
          </h2>
          <p className="text-[12px] text-muted">
            Hand long-running tasks to an autonomous Copilot agent.
            Get the runtime ready here without leaving Precursor.
          </p>
        </div>
        {runtimeCard}
        <button
          type="button"
          onClick={onOpenSettings}
          className="flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface"
        >
          <SettingsIcon size={14} /> Open Settings
        </button>
      </div>
    </div>
  );
}

// Disabled: send the user to Settings to turn the feature on.
export function AgentsDisabled({ onOpenSettings }: { onOpenSettings: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <Bot size={28} className="text-muted" />
      <div className="max-w-sm space-y-1">
        <p className="text-sm font-medium">Agents mode is off</p>
        <p className="text-[12px] text-muted">
          Hand long-running tasks to an autonomous Copilot agent, attached to a
          topic or chat. Turn it on in Settings to get started.
        </p>
      </div>
      <button
        type="button"
        onClick={onOpenSettings}
        className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white"
      >
        <SettingsIcon size={14} /> Open Settings
      </button>
    </div>
  );
}

// An agent is open but the runtime needs attention or the feature is off: the
// timeline stays readable under this strip.
export function AgentRuntimeStrip({
  needsRuntime,
  enabled,
  runtimeCard,
  onOpenSettings,
}: {
  needsRuntime: boolean;
  enabled: boolean;
  runtimeCard: ReactNode;
  onOpenSettings: () => void;
}) {
  if (!needsRuntime && enabled) return null;
  return (
    <div className="max-h-[50%] shrink-0 space-y-2 overflow-y-auto px-5 py-3">
      {needsRuntime && runtimeCard}
      {!enabled && <p className="text-[12px] text-muted">Agents mode is off. Your timeline is still available.</p>}
      <button type="button" onClick={onOpenSettings} className="text-[12px] text-accent">
        Open Settings
      </button>
    </div>
  );
}
