import { useEffect, useRef, useState } from "react";
import { History, Loader2, Save } from "lucide-react";
import { api } from "../../lib/api";
import { draftKey, useStoredDraft } from "../../lib/stepDrafts";
import { hasUnsavedEdits } from "../../lib/stepPlans";
import type { Step } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { Markdown } from "../Markdown";
import { RefineTextarea } from "../RefineTextarea";
import { BTN_SECONDARY, BTN_SMALL } from "./StepUi";
import { VersionHistory } from "./VersionHistory";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

/**
 * A leaf step's Markdown artifact: edit / preview, save, generate with the
 * model, and the version history with revert. Edits stay local until saved; a
 * newer version arriving while the draft is dirty never overwrites it silently.
 */
export function StepArtifactEditor({ step, mutate }: { step: Step; mutate: Mutate }) {
  const confirm = useConfirm();
  // What the draft started from: edits are measured against this, not the live
  // artifact, so a new version arriving (e.g. a model draft) isn't mistaken for
  // unsaved edits. Both outlive this component (see stepDrafts), so moving to
  // another step and back keeps unsaved text.
  const { draft, setDraft, base, setBase } = useStoredDraft(draftKey(step.plan_id, step.id), step.artifact);
  const [view, setView] = useState<"edit" | "preview">("edit");
  const [saving, setSaving] = useState(false);
  const [newerVersion, setNewerVersion] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const dirty = hasUnsavedEdits(draft, base);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const baseRef = useRef(base);
  baseRef.current = base;

  // A new version of this step (or, on return to it, one made while away):
  // follow it unless that would discard unsaved edits.
  useEffect(() => {
    if (dirtyRef.current && step.artifact.trim() !== draftRef.current.trim()) {
      setNewerVersion(step.artifact !== baseRef.current);
    } else {
      setDraft(step.artifact);
      setBase(step.artifact);
      setNewerVersion(false);
    }
  }, [step.id, step.artifact_version, step.artifact]);

  async function save(): Promise<void> {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await mutate(() => api.stepPlans.writeArtifact(step.id, draft));
    } finally {
      setSaving(false);
    }
  }

  async function confirmDiscard(what: string): Promise<boolean> {
    if (!dirty) return true;
    return confirm({
      title: "Discard unsaved edits?",
      message: `Your unsaved edits to this artifact will be lost when ${what}.`,
      confirmLabel: "Discard edits",
      variant: "warning",
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex overflow-hidden rounded-lg border border-border text-xs" role="tablist" aria-label="Artifact view">
          {(["edit", "preview"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={`px-3 py-1.5 ${view === v ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-surface"}`}
            >
              {v === "edit" ? "Edit" : "Preview"}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted">
          {step.artifact_version > 0 ? `Version ${step.artifact_version}` : "No artifact yet"}
          {dirty ? " · unsaved edits" : ""}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => setHistoryOpen((v) => !v)}
            className={BTN_SMALL}
            aria-expanded={historyOpen}
            disabled={step.artifact_version === 0}
            data-tooltip={step.artifact_version === 0 ? "No versions yet" : "Show earlier versions"}
          >
            <History size={13} /> History
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!dirty || saving}
            className={BTN_SECONDARY}
            data-tooltip={dirty ? "Save as a new version (⌘/Ctrl+S)" : "Nothing to save"}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            Save
          </button>
        </div>
      </div>

      {newerVersion && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          A newer version of this artifact exists.
          <button
            type="button"
            className="underline"
            onClick={() => {
              setDraft(step.artifact);
              setBase(step.artifact);
              setNewerVersion(false);
            }}
          >
            Discard my edits and load it
          </button>
        </div>
      )}

      {view === "edit" ? (
        <RefineTextarea
          value={draft}
          onValueChange={setDraft}
          refineKind="note"
          markdown
          rows={16}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
              e.preventDefault();
              void save();
            }
          }}
          placeholder="Write this step's result in Markdown, or draft it with the model (button above)."
          aria-label="Artifact (Markdown)"
          className="w-full resize-y rounded-lg border border-border bg-bg px-3 py-2 font-mono text-sm outline-none focus:border-accent/60"
        />
      ) : (
        <div className="min-h-40 rounded-lg border border-border bg-bg px-4 py-3">
          {draft.trim() ? (
            <Markdown>{draft}</Markdown>
          ) : (
            <p className="text-sm text-muted">Nothing to preview yet.</p>
          )}
        </div>
      )}

      {historyOpen && step.artifact_version > 0 && (
        <VersionHistory
          load={() => api.stepPlans.versions(step.id)}
          refreshKey={`${step.id}:${step.artifact_version}`}
          current={step.artifact_version}
          onRestore={async (v) => {
            if (!(await confirmDiscard(`you restore version ${v.version}`))) return;
            await mutate(() => api.stepPlans.revertArtifact(step.id, v.version));
          }}
        />
      )}
    </div>
  );
}
