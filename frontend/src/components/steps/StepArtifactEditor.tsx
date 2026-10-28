import { useCallback, useEffect, useRef, useState } from "react";
import { History, Loader2, Save } from "lucide-react";
import { api, apiErrorMessage } from "../../lib/api";
import { hasUnsavedEdits } from "../../lib/stepPlans";
import type { Step, StepArtifactVersion } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { Markdown } from "../Markdown";
import { RefineTextarea } from "../RefineTextarea";
import { BTN_SECONDARY, BTN_SMALL, ErrorBanner } from "./StepUi";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

/**
 * A leaf step's Markdown artifact: edit / preview, save, generate with the
 * model, and the version history with revert. Edits stay local until saved; a
 * newer version arriving while the draft is dirty never overwrites it silently.
 */
export function StepArtifactEditor({ step, mutate }: { step: Step; mutate: Mutate }) {
  const confirm = useConfirm();
  const [draft, setDraft] = useState(step.artifact);
  // What the draft started from: edits are measured against this, not the live
  // artifact, so a new version arriving (e.g. a model draft) isn't mistaken for
  // unsaved edits.
  const [base, setBase] = useState(step.artifact);
  const [view, setView] = useState<"edit" | "preview">("edit");
  const [saving, setSaving] = useState(false);
  const [newerVersion, setNewerVersion] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const dirty = hasUnsavedEdits(draft, base);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const lastStepId = useRef(step.id);

  // A different step, or a new version of this one: follow it unless that
  // would discard unsaved edits.
  useEffect(() => {
    const sameStep = step.id === lastStepId.current;
    lastStepId.current = step.id;
    if (sameStep && dirtyRef.current && step.artifact.trim() !== draftRef.current.trim()) {
      setNewerVersion(true);
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
        <VersionHistory step={step} mutate={mutate} confirmDiscard={confirmDiscard} />
      )}
    </div>
  );
}

function VersionHistory({
  step,
  mutate,
  confirmDiscard,
}: {
  step: Step;
  mutate: Mutate;
  confirmDiscard: (what: string) => Promise<boolean>;
}) {
  const [versions, setVersions] = useState<StepArtifactVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setVersions(await api.stepPlans.versions(step.id));
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load the history."));
    }
  }, [step.id]);

  useEffect(() => {
    void load();
  }, [load, step.artifact_version]);

  async function revert(v: StepArtifactVersion): Promise<void> {
    if (!(await confirmDiscard(`you restore version ${v.version}`))) return;
    await mutate(() => api.stepPlans.revertArtifact(step.id, v.version));
  }

  return (
    <section className="space-y-2 rounded-lg border border-border p-3" aria-label="Version history">
      <h4 className="text-xs font-medium uppercase tracking-wide text-muted">Version history</h4>
      {error && <ErrorBanner message={error} />}
      {!versions && !error && (
        <p className="flex items-center gap-2 text-sm text-muted">
          <Loader2 size={14} className="animate-spin" /> Loading…
        </p>
      )}
      <ul className="space-y-1.5">
        {versions?.map((v) => (
          <li key={v.version} className="rounded-lg border border-border">
            <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-sm">
              <span className="font-medium">v{v.version}</span>
              <span className="text-xs text-muted">
                {v.author === "model" ? "model" : "you"} · {new Date(v.created_at).toLocaleString()}
              </span>
              {v.version === step.artifact_version && (
                <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] text-accent">current</span>
              )}
              <span className="ml-auto flex gap-1.5">
                <button
                  type="button"
                  className={BTN_SMALL}
                  aria-expanded={shown === v.version}
                  onClick={() => setShown(shown === v.version ? null : v.version)}
                >
                  {shown === v.version ? "Hide" : "View"}
                </button>
                <button
                  type="button"
                  className={BTN_SMALL}
                  disabled={v.version === step.artifact_version}
                  data-tooltip={
                    v.version === step.artifact_version
                      ? "This is the current version"
                      : "Restore as a new version"
                  }
                  onClick={() => void revert(v)}
                >
                  Revert
                </button>
              </span>
            </div>
            {shown === v.version && (
              <div className="max-h-72 overflow-y-auto border-t border-border px-4 py-2">
                <Markdown>{v.content}</Markdown>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
