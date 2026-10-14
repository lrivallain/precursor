import { providerSetupIssue } from "../lib/modelPresetHealth";
import type { LLMProviderSpec, Settings } from "../lib/types";

export function ProviderConfigurationBadge({ settings, provider }: {
  settings: Settings | null;
  provider: LLMProviderSpec;
}) {
  const state = !settings ? "checking" : providerSetupIssue(settings, provider) ? "missing" : "configured";
  const label = state === "checking" ? "Checking configuration" : state === "configured" ? "Configured" : "Not configured";
  const color = state === "configured"
    ? "text-emerald-800 dark:text-emerald-300"
    : state === "missing" ? "text-amber-800 dark:text-amber-300" : "text-muted";
  const background = state === "configured" ? "bg-emerald-500/10" : state === "missing" ? "bg-amber-500/10" : "bg-border/40";
  return (
    <span
      data-provider-configuration={state}
      className={`shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ${color} ${background}`}
    >
      <span aria-hidden="true">{state === "configured" ? "\u2713" : "\u25cb"}</span>
      {" "}{label}
    </span>
  );
}
