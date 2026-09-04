import { Loader2 } from "lucide-react";

interface Props {
  /** What is being resolved, e.g. "Loading agents…". */
  label: string;
}

/**
 * Neutral placeholder for a section whose feature state isn't known yet.
 *
 * Settings arrive asynchronously, so a flag like `agents_enabled` reads `false`
 * for the width of that request. Rendering the section's "turn this on" empty
 * state in the meantime tells the user the feature is disabled a beat before
 * showing them their own agents — this says nothing instead.
 */
export function SectionLoading({ label }: Props) {
  return (
    <div
      className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-[12px] text-muted"
      role="status"
      aria-live="polite"
    >
      <Loader2 size={18} className="animate-spin" />
      {label}
    </div>
  );
}
