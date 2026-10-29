import { useEffect, useRef, useState, type ReactNode } from "react";
import { History, Loader2, Save } from "lucide-react";
import { draftKey, useStoredDraft } from "../../lib/stepDrafts";
import { hasUnsavedEdits } from "../../lib/stepPlans";
import { useConfirm } from "../ConfirmDialog";
import { Markdown } from "../Markdown";
import { RefineTextarea } from "../RefineTextarea";
import { EditPreviewTabs, type EditPreview } from "./EditPreviewTabs";
import { BTN_SECONDARY, BTN_SMALL } from "./StepUi";
import { VersionHistory, type VersionRow } from "./VersionHistory";

/**
 * The unsaved text of a versioned document (a step's artifact, a plan's final
 * output). It is measured against what it started from, not the live text, so a
 * new version arriving (a model draft) isn't mistaken for the user's edits; and
 * both outlive the editor (see stepDrafts), so moving elsewhere and back keeps
 * the text. A newer version arriving while the draft is dirty never overwrites
 * it: `newer` says so, and `loadNewer` is the explicit way out.
 */
export function useDocumentDraft(planId: number, id: number | "final", persisted: string) {
  const { draft, setDraft, base, setBase } = useStoredDraft(draftKey(planId, id), persisted);
  const [newer, setNewer] = useState(false);
  const dirty = hasUnsavedEdits(draft, base);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const baseRef = useRef(base);
  baseRef.current = base;

  useEffect(() => {
    if (dirtyRef.current && persisted.trim() !== draftRef.current.trim()) {
      setNewer(persisted !== baseRef.current);
    } else {
      setDraft(persisted);
      setBase(persisted);
      setNewer(false);
    }
  }, [planId, id, persisted]);

  function loadNewer(): void {
    setDraft(persisted);
    setBase(persisted);
    setNewer(false);
  }

  return { draft, setDraft, dirty, newer, loadNewer };
}

export type DocumentDraft = ReturnType<typeof useDocumentDraft>;

/**
 * One panel for every versioned text of a plan: version label, history with
 * restore, save, edit / preview and the "newer version" guard. `noun` is what
 * the user calls it ("artifact", "final output").
 */
export function DocumentEditor({
  doc,
  persisted,
  version,
  versions,
  noun,
  placeholder,
  onSave,
  onRestore,
  status,
  actions,
  unchangedSave,
}: {
  doc: DocumentDraft;
  /** The saved text: what Save writes when nothing was edited. */
  persisted: string;
  version: number;
  versions: VersionRow[];
  noun: string;
  placeholder: string;
  onSave: (text: string) => Promise<unknown>;
  onRestore: (version: number) => Promise<unknown>;
  /** Beside the version label (e.g. where it was posted). */
  status?: ReactNode;
  /** After Save in the toolbar. */
  actions?: ReactNode;
  /** Lets Save run on unchanged text, e.g. "Mark as reviewed". */
  unchangedSave?: { label: string; tooltip: string };
}) {
  const confirm = useConfirm();
  const { draft, setDraft, dirty, newer, loadNewer } = doc;
  const [view, setView] = useState<EditPreview>("edit");
  const [saving, setSaving] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const canSave = dirty || unchangedSave !== undefined;

  async function save(): Promise<void> {
    if (!canSave || saving) return;
    setSaving(true);
    try {
      await onSave(dirty ? draft : persisted);
    } finally {
      setSaving(false);
    }
  }

  async function restore(v: VersionRow): Promise<void> {
    if (dirty) {
      const ok = await confirm({
        title: "Discard unsaved edits?",
        message: `Your unsaved edits to the ${noun} will be lost when you restore version ${v.version}.`,
        confirmLabel: "Discard edits",
        variant: "warning",
      });
      if (!ok) return;
    }
    await onRestore(v.version);
  }

  const showReview = unchangedSave !== undefined && !dirty;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted">
          {version > 0 ? <span className="font-medium text-fg">Version {version}</span> : <span>No {noun} yet</span>}
          {status}
        </span>
        {dirty && <span className="text-xs text-muted">unsaved edits</span>}
        <div className="ml-auto flex items-center gap-2">
          {versions.length > 0 && (
            <button
              type="button"
              onClick={() => setHistoryOpen((v) => !v)}
              aria-expanded={historyOpen}
              className={BTN_SMALL}
              data-tooltip={`Earlier versions of the ${noun}`}
            >
              <History size={13} /> History
            </button>
          )}
          <button
            type="button"
            onClick={() => void save()}
            disabled={!canSave || saving}
            className={BTN_SECONDARY}
            data-tooltip={
              showReview ? unchangedSave.tooltip : dirty ? "Save as a new version (⌘/Ctrl+S)" : "Nothing to save"
            }
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {showReview ? unchangedSave.label : "Save"}
          </button>
          {actions}
        </div>
      </div>

      {newer && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          A newer version of this {noun} exists.
          <button type="button" className="underline" onClick={loadNewer}>
            Discard my edits and load it
          </button>
        </div>
      )}

      <div className="space-y-1.5">
        <EditPreviewTabs view={view} onChange={setView} label={`${noun} view`} />
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
            placeholder={placeholder}
            aria-label={`${noun[0].toUpperCase()}${noun.slice(1)} (Markdown)`}
            className="w-full resize-y rounded-lg border border-border bg-bg px-3 py-2 font-mono text-sm outline-none focus:border-accent/60"
          />
        ) : (
          <div className="min-h-80 resize-y overflow-auto rounded-lg border border-border bg-bg px-4 py-3">
            {draft.trim() ? <Markdown>{draft}</Markdown> : <p className="text-sm text-muted">Nothing to preview yet.</p>}
          </div>
        )}
      </div>

      {historyOpen && versions.length > 0 && (
        <VersionHistory load={async () => versions} refreshKey={versions} current={version} onRestore={restore} />
      )}
    </div>
  );
}
