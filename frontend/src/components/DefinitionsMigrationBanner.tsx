import { useCallback, useEffect, useState } from "react";
import { ArrowRight, FileCode2, Loader2 } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { notifyDefinitionsChanged, subscribeDefinitionsChanged } from "../lib/definitionsEvents";
import type { DefinitionsStatus } from "../lib/types";

// "Not now" hides the invitation for a while rather than for good: a week later
// the agents and workflows are still where they were.
const DISMISS_KEY = "precursor:definitions-banner:dismissed-at";
const DISMISS_MS = 7 * 24 * 60 * 60 * 1000;

function dismissedRecently(): boolean {
  try {
    const at = Number(window.localStorage.getItem(DISMISS_KEY));
    return Number.isFinite(at) && at > 0 && Date.now() - at < DISMISS_MS;
  } catch {
    return false;
  }
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * On top of the Agents and Workflows homes: an invitation to move agents and
 * workflows still declared by the database into definition files. Shows
 * nothing once there's nothing left to migrate, or for a week after "Not now".
 */
export function DefinitionsMigrationBanner({
  onOpenWizard,
  refreshKey,
}: {
  /** Opens Settings → Definition files. */
  onOpenWizard: () => void;
  /** Changes when the list below does (an agent added or removed). */
  refreshKey?: unknown;
}) {
  const [status, setStatus] = useState<DefinitionsStatus | null>(null);
  const [hidden, setHidden] = useState(dismissedRecently);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.definitions
      .status()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);
  useEffect(() => subscribeDefinitionsChanged(load), [load]);

  if (hidden || !status) return null;
  const pending = status.agents + status.workflows;
  if (status.stage === "finalized" || pending === 0) return null;

  const what = [
    status.agents ? plural(status.agents, "agent") : null,
    status.workflows ? plural(status.workflows, "workflow") : null,
  ]
    .filter(Boolean)
    .join(" and ");

  // Migrated already, a few rows still have no file: write theirs in place.
  async function writeMissing(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.definitions.export(false);
      notifyDefinitionsChanged();
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function dismiss(): void {
    try {
      window.localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      // Private mode: hide for this visit only.
    }
    setHidden(true);
  }

  const leftovers = status.stage === "files";

  return (
    <div
      role="region"
      aria-label="Definition files"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-sky-500/30 bg-sky-500/10 px-4 py-2 text-[12px] text-sky-900 dark:text-sky-100"
    >
      <FileCode2 size={15} className="shrink-0 text-sky-600 dark:text-sky-400" aria-hidden />
      <p className="min-w-0 flex-1">
        {leftovers ? (
          <>
            <strong className="font-medium">{what}</strong>{" "}
            {pending === 1 ? "has" : "have"} no definition file yet, so the database still declares{" "}
            {pending === 1 ? "it" : "them"}.
          </>
        ) : (
          <>
            <strong className="font-medium">Your {what}</strong> {pending === 1 ? "is" : "are"}{" "}
            declared by the database. Move {pending === 1 ? "it" : "them"} into YAML files you can
            edit, review and keep in git — a guided, reversible migration.
          </>
        )}
        <span className="ml-1.5 rounded bg-amber-500/15 px-1 py-px text-[10px] font-medium text-amber-700 dark:text-amber-300">
          Work in progress
        </span>
        {error && <span className="ml-2 text-red-600 dark:text-red-400">{error}</span>}
      </p>
      <div className="flex shrink-0 items-center gap-1.5">
        {leftovers ? (
          <button
            type="button"
            onClick={() => void writeMissing()}
            disabled={busy}
            className="inline-flex items-center gap-1 rounded bg-sky-600 px-2.5 py-1 font-medium text-white hover:bg-sky-700 disabled:opacity-50"
          >
            {busy && <Loader2 size={12} className="animate-spin" aria-hidden />}
            Write {pending === 1 ? "its file" : "their files"}
          </button>
        ) : (
          <button
            type="button"
            onClick={onOpenWizard}
            className="inline-flex items-center gap-1 rounded bg-sky-600 px-2.5 py-1 font-medium text-white hover:bg-sky-700"
          >
            Start the migration <ArrowRight size={12} aria-hidden />
          </button>
        )}
        <button
          type="button"
          onClick={dismiss}
          data-tooltip="Hide this for a week"
          className="rounded px-2 py-1 text-sky-800 hover:bg-sky-500/15 dark:text-sky-200"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
