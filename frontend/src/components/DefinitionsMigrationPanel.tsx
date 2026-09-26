import { useCallback, useEffect, useState } from "react";
import { ArrowRightLeft, FileCode2, FolderOpen, Loader2, RotateCcw } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { openWorkspaceFile } from "../lib/workspaceLink";
import type {
  MigrationAction,
  MigrationPreview,
  MigrationResult,
  RevertResult,
} from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

const ACTION_LABEL: Record<MigrationAction, string> = {
  create: "new file",
  regenerate: "rewritten from the database",
  unchanged: "already up to date",
  new_from_disk: "added from the folder",
};

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * Settings → Workflows: move agents and workflows from the database to their
 * definition files (work in progress, see docs/definitions.md) — and back.
 */
export function DefinitionsMigrationPanel() {
  const confirmAction = useConfirm();
  const [preview, setPreview] = useState<MigrationPreview | null>(null);
  const [busy, setBusy] = useState<"load" | "migrate" | "revert" | null>("load");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [showItems, setShowItems] = useState(false);

  const load = useCallback(async () => {
    setBusy("load");
    setError(null);
    try {
      setPreview(await api.definitions.migration());
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = (action: MigrationAction) =>
    preview?.items.filter((i) => i.action === action).length ?? 0;
  const errors = preview?.issues.filter((i) => i.severity === "error") ?? [];
  const warnings = preview?.issues.filter((i) => i.severity === "warning") ?? [];
  const firstFile = preview?.sample_path ?? null;

  async function migrate(): Promise<void> {
    if (!preview) return;
    const rewritten = counts("regenerate");
    const lines = [
      `${plural(preview.items.filter((i) => i.action !== "new_from_disk").length, "agent or workflow")} will be declared by files in ${preview.workspace?.name ?? preview.folder}.`,
      rewritten
        ? `${plural(rewritten, "file")} that differ from the database will be rewritten from it.`
        : null,
      errors.length ? `${plural(errors.length, "definition")} with errors will refuse to run until fixed.` : null,
      "A copy of the database is taken first, and you can switch back from here.",
    ].filter(Boolean);
    const ok = await confirmAction({
      title: "Migrate to definition files?",
      message: lines.join("\n"),
      confirmLabel: "Migrate",
    });
    if (!ok) return;
    setBusy("migrate");
    setError(null);
    setDone(null);
    try {
      const result: MigrationResult = await api.definitions.migrate(true);
      if (result.ok) {
        setDone(
          `Migrated: ${plural(result.created, "file")} written, ${result.regenerated} rewritten, ` +
            `${result.unchanged} kept.` +
            (result.snapshot ? ` Database copy: ${result.snapshot}` : ""),
        );
      } else {
        setError(
          `The files didn't verify, so nothing was switched:\n• ${result.mismatches.join("\n• ")}`,
        );
      }
      await load();
    } catch (e) {
      setError(apiErrorMessage(e));
      setBusy(null);
    }
  }

  async function revert(): Promise<void> {
    const ok = await confirmAction({
      title: "Switch back to the database?",
      message:
        "Every file's declaration is copied into the database, which then declares agents and workflows again. The files stay where they are.",
      confirmLabel: "Switch back",
      variant: "warning",
    });
    if (!ok) return;
    setBusy("revert");
    setError(null);
    setDone(null);
    try {
      const result: RevertResult = await api.definitions.revert();
      setDone(
        `Switched back: ${plural(result.agents, "agent")} and ${plural(result.workflows, "workflow")} copied into the database.` +
          (result.skipped.length ? ` Skipped: ${result.skipped.join("; ")}` : ""),
      );
      await load();
    } catch (e) {
      setError(apiErrorMessage(e));
      setBusy(null);
    }
  }

  return (
    <section className="space-y-2" aria-label="Definition files">
      <h3 className="flex items-center gap-2 text-sm font-medium">
        <FileCode2 size={14} className="text-accent" aria-hidden />
        Definition files
        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
          Work in progress
        </span>
      </h3>
      <p className="text-[11px] text-muted">
        Declare agents and workflows in YAML files you can edit, review and keep
        in git; the database then keeps only what happens when they run.
      </p>

      {busy === "load" && !preview && (
        <p className="flex items-center gap-1.5 text-[12px] text-muted">
          <Loader2 size={12} className="animate-spin" /> Checking…
        </p>
      )}

      {preview && (
        <div className="space-y-2 rounded border border-border px-3 py-2 text-[12px]">
          <p>
            Declared by{" "}
            <strong>{preview.source === "files" ? "definition files" : "the database"}</strong>
            {preview.forced && (
              <span className="text-muted"> (forced by PRECURSOR_DEFINITIONS_SOURCE)</span>
            )}
            .{" "}
            {preview.workspace && (
              <span className="text-muted">
                Files live in the <strong>{preview.workspace.name}</strong> workspace.
              </span>
            )}
          </p>
          {preview.workspace && firstFile && (
            <button
              type="button"
              onClick={() => openWorkspaceFile(preview.workspace!.slug, firstFile)}
              className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
            >
              <FolderOpen size={12} aria-hidden /> Open in Files
            </button>
          )}

          {preview.source === "database" && (
            <>
              <p className="text-muted">
                {[
                  counts("create") && `${plural(counts("create"), "new file")}`,
                  counts("regenerate") && `${counts("regenerate")} to rewrite`,
                  counts("unchanged") && `${counts("unchanged")} already up to date`,
                  counts("new_from_disk") && `${counts("new_from_disk")} to add from the folder`,
                ]
                  .filter(Boolean)
                  .join(" · ") || "Nothing to migrate yet."}
                {preview.items.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowItems((v) => !v)}
                    className="ml-2 text-accent hover:underline"
                  >
                    {showItems ? "Hide details" : "Details"}
                  </button>
                )}
              </p>
              {showItems && (
                <ul className="max-h-48 space-y-0.5 overflow-auto text-[11px]">
                  {preview.items.map((item, i) => (
                    <li key={i}>
                      <span className="text-muted">{item.kind}</span> {item.name} —{" "}
                      {ACTION_LABEL[item.action]}
                      {item.reason && item.action === "regenerate" && (
                        <span className="text-muted"> ({item.reason})</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {[...errors, ...warnings].length > 0 && (
                <ul className="max-h-32 space-y-0.5 overflow-auto text-[11px]">
                  {[...errors, ...warnings].map((issue, i) => (
                    <li key={i}>
                      <span
                        className={issue.severity === "error" ? "text-red-500" : "text-amber-500"}
                      >
                        {issue.severity}
                      </span>{" "}
                      {issue.path && <code className="text-muted">{issue.path}</code>}{" "}
                      {issue.message}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          {preview.blockers.length > 0 && preview.source === "database" && (
            <ul className="space-y-0.5 text-[11px] text-amber-700 dark:text-amber-300">
              {preview.blockers.map((b, i) => (
                <li key={i}>Can't migrate yet: {b}</li>
              ))}
            </ul>
          )}

          <div className="flex items-center gap-2 pt-1">
            {preview.source === "database" ? (
              <button
                type="button"
                disabled={!preview.ready || busy !== null}
                onClick={() => void migrate()}
                className="inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1 text-[12px] font-medium text-white disabled:opacity-50"
              >
                {busy === "migrate" ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <ArrowRightLeft size={12} aria-hidden />
                )}
                Migrate to files
              </button>
            ) : (
              <button
                type="button"
                disabled={preview.forced || busy !== null}
                onClick={() => void revert()}
                data-tooltip={
                  preview.forced ? "Unset PRECURSOR_DEFINITIONS_SOURCE to switch back" : undefined
                }
                className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-[12px] disabled:opacity-50"
              >
                {busy === "revert" ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <RotateCcw size={12} aria-hidden />
                )}
                Switch back to the database
              </button>
            )}
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void load()}
              className="text-[11px] text-muted hover:text-fg disabled:opacity-50"
            >
              Check again
            </button>
          </div>
        </div>
      )}

      {done && <p className="text-[12px] text-emerald-600 dark:text-emerald-400">{done}</p>}
      {error && <p className="whitespace-pre-line text-[12px] text-red-500">{error}</p>}
    </section>
  );
}
