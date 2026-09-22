import { useState } from "react";
import { CheckCircle2, Download, Loader2, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import type { AgentRuntimeStatus } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

interface Props {
  status: AgentRuntimeStatus | null;
  enabled: boolean;
  loading: boolean;
  error: string | null;
  onRefresh: () => Promise<void>;
  onUpdate: (status: AgentRuntimeStatus) => Promise<void>;
}

export function AgentsRuntimeCard({
  status,
  enabled,
  loading,
  error: statusError,
  onRefresh,
  onUpdate,
}: Props) {
  const confirmAction = useConfirm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const job = status?.job;
  const running = job?.state === "running";
  const awaitingRestart =
    enabled && job?.state === "succeeded" && !job.runtime_started && !status?.available;
  const needsRestart = enabled && status?.available && !status.runtime_started && !running;

  async function install(): Promise<void> {
    if (
      !(await confirmAction({
        title: "Install the Copilot CLI",
        message:
          "This downloads the native Copilot CLI (~90 MB, ~145 MB on disk) from " +
          "GitHub into the SDK's cache. It runs in the background - you can keep " +
          "using Precursor while it does. Your Agents enable/disable preference is preserved.",
        confirmLabel: "Download",
      }))
    ) return;
    setBusy(true);
    setError(null);
    try {
      await onUpdate(await api.agents.installCli());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function restart(): Promise<void> {
    if (
      !(await confirmAction({
        title: "Restart Precursor",
        message:
          "Precursor restarts to pick up the runtime. The page reconnects on its " +
          "own once it's back; anything mid-stream is interrupted.",
        confirmLabel: "Restart",
      }))
    ) return;
    setBusy(true);
    setError(null);
    try {
      await api.agents.restartForRuntime();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Copilot runtime" className="space-y-2 rounded border border-border bg-surface/50 p-3">
      <h3 className="flex items-center gap-1.5 text-sm font-medium">
        <Download size={14} /> Copilot runtime
      </h3>

      {!status && !statusError && (
        <p role="status" className="flex items-center gap-1.5 text-[12px] text-muted">
          <Loader2 size={13} className="animate-spin" /> Loading runtime status...
        </p>
      )}
      {statusError && (
        <div role="alert" className="space-y-2 text-[12px] text-red-500">
          <p>{statusError}</p>
          <button
            type="button"
            onClick={() => void onRefresh()}
            disabled={loading}
            className="rounded border border-border px-2.5 py-1.5 disabled:opacity-40"
          >
            Retry runtime status
          </button>
        </div>
      )}
      {status && !status.available && (
        status.sdk_installed ? (
          <p className="text-[12px] text-muted">
            Agents need the native Copilot CLI. Install it here to get started.
            Precursor only downloads it after you confirm.
          </p>
        ) : (
          <p role="alert" className="text-[12px] text-red-500">{status.unavailable_reason}</p>
        )
      )}
      {status?.available && !running && !needsRestart && !statusError && !loading && (
        <p className="flex items-center gap-1.5 text-[12px] text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 size={13} />
          {enabled
            ? "The Copilot runtime is ready."
            : "The Copilot CLI is installed. Turn on Agents mode to use it."}
        </p>
      )}
      {needsRestart && (
        <p className="text-[12px] text-amber-700 dark:text-amber-300">
          The Copilot runtime is installed but didn&apos;t start in this process.
          Restart Precursor to start the runtime; no download is needed.
          Interrupted agents can then be resumed.
        </p>
      )}
      {status?.cli_path && status.available && (
        <p className="break-all font-mono text-[11px] text-muted">{status.cli_path}</p>
      )}
      {running && (
        <p role="status" className="flex items-center gap-1.5 text-[12px] text-muted">
          <Loader2 size={13} className="animate-spin" /> {job.detail}
        </p>
      )}
      {job?.state === "failed" && (
        <div role="alert" className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[12px] text-red-600 dark:text-red-400">
          <p>{job.detail}</p>
          {job.error && <p className="mt-1 break-all font-mono">{job.error}</p>}
        </div>
      )}
      {awaitingRestart && (
        <p className="text-[12px] text-amber-700 dark:text-amber-300">{job?.detail}</p>
      )}
      {error && <p role="alert" className="text-[12px] text-red-500">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        {status?.can_install_cli && !status.available && !awaitingRestart && (
          <button
            type="button"
            onClick={() => void install()}
            disabled={busy || running || loading || !!statusError}
            className="flex items-center gap-1.5 rounded border border-violet-500/30 bg-violet-500/15 px-2.5 py-1.5 text-[12px] font-medium text-violet-700 hover:bg-violet-500/25 disabled:opacity-40 dark:text-violet-300"
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
            Install the Copilot CLI (~90 MB)
          </button>
        )}
        {(needsRestart || awaitingRestart) && status?.can_restart && (
          <button
            type="button"
            onClick={() => void restart()}
            disabled={busy || loading || !!statusError}
            className="flex items-center gap-1.5 rounded border border-border px-2.5 py-1.5 text-[12px] disabled:opacity-40"
          >
            <RefreshCw size={13} /> Restart now
          </button>
        )}
      </div>

      {(needsRestart || awaitingRestart) && status && !status.can_restart && (
        <p className="text-[12px] text-muted">
          {status.restart_blocked_reason} Run{" "}
          <code className="font-mono">precursor service restart</code> when convenient.
        </p>
      )}
      {status && !status.can_install_cli && status.sdk_installed && !status.available && (
        <p className="text-[12px] text-muted">
          {status.install_blocked_reason} You can still point{" "}
          <code className="font-mono">COPILOT_CLI_PATH</code> at an existing
          Copilot CLI, or install one so <code className="font-mono">copilot</code>{" "}
          is on <code className="font-mono">PATH</code>.
        </p>
      )}
    </section>
  );
}
