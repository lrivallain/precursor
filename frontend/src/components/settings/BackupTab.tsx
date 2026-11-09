import type { BackupRunResult } from "../../lib/types";
import { NumberInput } from "./NumberInput";

export function BackupTab({
  enabled,
  setEnabled,
  dir,
  setDir,
  retention,
  setRetention,
  lastRunAt,
  lastStatus,
  lastError,
  running,
  runResult,
  onRunNow,
}: {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
  dir: string;
  setDir: (v: string) => void;
  retention: number;
  setRetention: (v: number) => void;
  lastRunAt: string | null;
  lastStatus: string | null;
  lastError: string | null;
  running: boolean;
  runResult: BackupRunResult | null;
  onRunNow: () => void;
}) {
  const lastRunLabel = lastRunAt ? new Date(lastRunAt).toLocaleString() : "Never";
  return (
    <div className="space-y-6">
      <p className="text-[11px] text-muted">
        Copy the database and attachment files into a folder on a daily schedule.
        Point this at a cloud-synced directory (e.g. OneDrive, Dropbox, iCloud
        Drive) to keep an off-machine copy. The live database stays on local disk —
        only a consistent snapshot is written to the folder, which is safe to sync.
      </p>

      <section className="space-y-3">
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">Enable daily backup</span>
            <span className="block text-[11px] text-muted">
              Runs about once every 24 hours while the app is running.
            </span>
          </span>
        </label>

        <div>
          <label className="block text-xs text-muted mb-1">Backup folder</label>
          <input
            type="text"
            value={dir}
            onChange={(e) => setDir(e.target.value)}
            placeholder="/Users/you/OneDrive/precursor-backups"
            className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          <p className="text-[11px] text-muted mt-1">
            Absolute path to an existing, writable directory. Snapshots go under{" "}
            <code className="font-mono">db/</code>, attachment files under{" "}
            <code className="font-mono">blobs/</code> and zipped definitions under{" "}
            <code className="font-mono">definitions/</code>.
          </p>
        </div>

        <div>
          <label className="block text-xs text-muted mb-1">
            Snapshots to keep
          </label>
          <NumberInput
            value={retention}
            min={1}
            max={3650}
            onCommit={setRetention}
          />
          <p className="text-[11px] text-muted mt-1">
            Older dated database snapshots are pruned. Attachment files are kept
            indefinitely (they are de-duplicated, so this is cheap).
          </p>
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Status</h3>
        <div className="text-[11px] text-muted">
          <div>
            Last run: <span className="text-fg">{lastRunLabel}</span>
            {lastStatus && (
              <span
                className={
                  lastStatus === "ok"
                    ? " text-emerald-600 dark:text-emerald-400"
                    : " text-red-600 dark:text-red-400"
                }
              >
                {" "}
                ({lastStatus})
              </span>
            )}
          </div>
          {lastError && (
            <div className="text-red-600 dark:text-red-400 mt-1">{lastError}</div>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onRunNow}
            disabled={running || !dir}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface disabled:opacity-50"
          >
            {running ? "Backing up..." : "Back up now"}
          </button>
          {!dir && (
            <span className="text-[11px] text-muted">
              Set a backup folder first.
            </span>
          )}
        </div>

        {runResult && (
          <div
            className={
              "text-[11px] rounded border px-3 py-2 " +
              (runResult.ok
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300")
            }
          >
            {runResult.detail}
          </div>
        )}
      </section>
    </div>
  );
}
