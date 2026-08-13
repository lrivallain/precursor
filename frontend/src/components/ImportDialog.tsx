import { AlertTriangle, Check, FileUp, Loader2, Upload, X } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { api, apiErrorMessage } from "../lib/api";
import type {
  ConflictAction,
  TransferConflict,
  TransferImportResult,
  TransferPreview,
} from "../lib/types";
import { Modal } from "./Modal";

interface Props {
  /** Narrows the dialog's copy and rejects the other kind of file. */
  expect?: "workflow" | "agent";
  onClose: () => void;
  onImported: (result: TransferImportResult) => void;
}

const ACTION_LABEL: Record<ConflictAction, string> = {
  link: "Use existing",
  replace: "Replace",
  create: "Create new",
};

const ACTION_HINT: Record<ConflictAction, string> = {
  link: "Point at the agent you already have and leave it untouched.",
  replace: "Overwrite its definition — every workflow using it follows.",
  create: "Keep both: import a separate copy under a new name.",
};

/** Key a conflict by kind + index, matching how the API addresses resolutions. */
function conflictKey(c: TransferConflict): string {
  return `${c.kind}:${c.index ?? "self"}`;
}

/**
 * Drop a YAML file, see exactly what it would do, then decide.
 *
 * The two-phase flow exists for the middle screen: an incoming agent whose name
 * already exists here is a genuine fork in the road — reuse the one you have,
 * let the file overwrite it, or keep both — and that choice can only be offered
 * once the collisions are known. Nothing is written until "Import" is pressed.
 */
export function ImportDialog({ expect, onClose, onImported }: Props) {
  const [content, setContent] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [preview, setPreview] = useState<TransferPreview | null>(null);
  const [choices, setChoices] = useState<Record<string, ConflictAction>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      try {
        const text = await file.text();
        const result = await api.transfer.preview(text);
        if (expect && result.kind !== expect) {
          setError(`That file contains ${result.kind === "agent" ? "an agent" : "a workflow"}.`);
          return;
        }
        setContent(text);
        setFilename(file.name);
        setPreview(result);
        setChoices(
          Object.fromEntries(result.conflicts.map((c) => [conflictKey(c), c.default])),
        );
      } catch (e) {
        setError(apiErrorMessage(e, "Couldn't read that file"));
      } finally {
        setBusy(false);
      }
    },
    [expect],
  );

  async function confirm(): Promise<void> {
    if (!content || !preview) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.transfer.import(
        content,
        preview.conflicts.map((c) => ({
          kind: c.kind,
          index: c.index,
          action: choices[conflictKey(c)] ?? c.default,
        })),
      );
      onImported(result);
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e, "Import failed"));
      setBusy(false);
    }
  }

  const agentConflicts = preview?.conflicts.filter((c) => c.kind === "agent") ?? [];
  const workflowConflict = preview?.conflicts.find((c) => c.kind === "workflow");

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      labelledBy="import-dialog-title"
      panelClassName="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-xl"
    >
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <h2 id="import-dialog-title" className="text-sm font-semibold text-fg">
          Import {expect === "agent" ? "agent" : expect === "workflow" ? "workflow" : "from YAML"}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1 text-muted transition hover:bg-white/5 hover:text-fg"
          aria-label="Close"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {!preview ? (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files[0];
              if (file) void load(file);
            }}
            className={`flex flex-col items-center justify-center rounded-2xl border border-dashed px-6 py-12 text-center transition ${
              dragging ? "border-indigo-500 bg-indigo-500/5" : "border-border"
            }`}
          >
            {busy ? (
              <Loader2 size={22} className="animate-spin text-muted" />
            ) : (
              <FileUp size={22} className="text-muted" />
            )}
            <p className="mt-3 text-sm font-medium text-fg">Drop a .yaml file here</p>
            <p className="mt-1 max-w-xs text-xs text-muted">
              An exported workflow arrives with the agents its steps need.
            </p>
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="mt-4 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-fg transition hover:bg-white/5"
            >
              Choose a file
            </button>
            <input
              ref={inputRef}
              type="file"
              accept=".yaml,.yml,application/yaml,text/yaml"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void load(file);
                e.target.value = "";
              }}
            />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="rounded-xl border border-border bg-white/5 px-4 py-3">
              <p className="text-sm font-semibold text-fg">{preview.name}</p>
              <p className="mt-0.5 text-xs text-muted">
                {preview.kind === "workflow"
                  ? `${preview.step_count} step${preview.step_count === 1 ? "" : "s"} · ${preview.agent_count} agent${preview.agent_count === 1 ? "" : "s"}`
                  : "Agent"}
                {filename ? ` · ${filename}` : ""}
              </p>
            </div>

            {workflowConflict && (
              <ConflictRow
                conflict={workflowConflict}
                value={choices[conflictKey(workflowConflict)] ?? workflowConflict.default}
                onChange={(action) =>
                  setChoices((c) => ({ ...c, [conflictKey(workflowConflict)]: action }))
                }
                title={`A workflow named “${workflowConflict.existing_title}” already exists`}
              />
            )}

            {agentConflicts.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium text-fg">
                  {agentConflicts.length} agent{agentConflicts.length === 1 ? "" : "s"} already
                  exist here
                </p>
                {agentConflicts.map((c) => (
                  <ConflictRow
                    key={conflictKey(c)}
                    conflict={c}
                    value={choices[conflictKey(c)] ?? c.default}
                    onChange={(action) =>
                      setChoices((prev) => ({ ...prev, [conflictKey(c)]: action }))
                    }
                    title={c.name}
                  />
                ))}
              </div>
            )}

            {preview.conflicts.length === 0 && (
              <p className="flex items-center gap-1.5 text-xs text-emerald-500">
                <Check size={13} /> Nothing here conflicts — this imports cleanly.
              </p>
            )}

            {preview.warnings.map((w) => (
              <p key={w.code + w.message} className="flex items-start gap-1.5 text-xs text-amber-500">
                <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {w.message}
              </p>
            ))}
          </div>
        )}

        {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
      </div>

      {preview && (
        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={() => {
              setPreview(null);
              setContent(null);
              setFilename(null);
              setError(null);
            }}
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition hover:bg-white/5"
          >
            Choose another file
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void confirm()}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-600 disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
            Import
          </button>
        </div>
      )}
    </Modal>
  );
}

function ConflictRow({
  conflict,
  value,
  onChange,
  title,
}: {
  conflict: TransferConflict;
  value: ConflictAction;
  onChange: (action: ConflictAction) => void;
  title: string;
}) {
  return (
    <div className="rounded-xl border border-border px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-fg">{title}</p>
          <p className="mt-0.5 text-[11px] text-muted">
            {conflict.same_object
              ? "This is the same one you exported."
              : `Matches the existing “${conflict.existing_title}” by name.`}
          </p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {conflict.allowed.map((action) => (
          <button
            key={action}
            type="button"
            onClick={() => onChange(action)}
            title={ACTION_HINT[action]}
            className={`rounded-lg border px-2.5 py-1 text-[11px] font-medium transition ${
              value === action
                ? "border-indigo-500 bg-indigo-500/10 text-indigo-500"
                : "border-border text-muted hover:bg-white/5"
            }`}
          >
            {ACTION_LABEL[action]}
          </button>
        ))}
      </div>
      <p className="mt-1.5 text-[11px] text-muted">{ACTION_HINT[value]}</p>
      {value === "replace" && conflict.workflow_count > 1 && (
        <p className="mt-1 flex items-start gap-1.5 text-[11px] text-amber-500">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          Used by {conflict.workflow_count} workflows — all of them will pick this up.
        </p>
      )}
    </div>
  );
}
