import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { useConfirm } from "./ConfirmDialog";
import { Markdown } from "./Markdown";
import type { TopicSummary, TopicSummaryHunk } from "../lib/types";

interface Props {
  summary: TopicSummary;
  busy: boolean;
  error: string | null;
  onSave: (content: string, revision: string) => Promise<boolean>;
  onResolve: (accepted: number[], revision: string) => Promise<boolean>;
  onRemove: (revision: string) => Promise<boolean>;
  onRefresh: () => void;
  onToggleVisible: () => void;
  onDismissError: () => void;
}

/**
 * The topic's status brief: a collapsible area above the transcript holding
 * where the topic stands, its open actions and the information needed to act.
 *
 * Three states in one panel:
 *  - *read* — rendered markdown, with refresh / edit / delete actions;
 *  - *edit* — a plain textarea; saving marks the brief as user-owned so a later
 *    refresh can never silently overwrite it;
 *  - *review* — when a refresh lands on a user-edited brief the model's version
 *    arrives as a list of changes, each accepted or refused on its own before
 *    anything is written.
 */
export function TopicSummaryPanel({
  summary,
  busy,
  error,
  onSave,
  onResolve,
  onRemove,
  onRefresh,
  onToggleVisible,
  onDismissError,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(summary.content);
  const [draftRevision, setDraftRevision] = useState(summary.revision);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const confirm = useConfirm();

  const suggestion = summary.suggestion;
  const hunks = suggestion?.hunks ?? [];

  useEffect(() => {
    if (editing) textareaRef.current?.focus();
  }, [editing]);

  async function save(): Promise<void> {
    if (await onSave(draft, draftRevision)) setEditing(false);
  }

  async function remove(): Promise<void> {
    if (await confirm({
      title: "Delete summary?",
      message: "The summary and any pending suggestions will be permanently deleted.",
      confirmLabel: "Delete",
      variant: "danger",
    })) {
      await onRemove(summary.revision);
    }
  }

  const collapsed = !summary.visible;

  return (
    <div data-summary-panel className="border-b border-border bg-surface/40">
      <div className="flex items-center gap-1.5 px-3 py-1.5">
        <button
          type="button"
          onClick={onToggleVisible}
          disabled={busy}
          aria-expanded={summary.visible}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-[11px] uppercase tracking-wide text-muted hover:text-fg"
        >
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          <FileText size={12} />
          <span>Summary</span>
          {suggestion && (
            <span className="rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] normal-case tracking-normal text-accent">
              {hunks.length} suggested change{hunks.length === 1 ? "" : "s"}
            </span>
          )}
        </button>
        {!collapsed && !editing && (
          <>
            <button
              type="button"
              className="rounded p-1.5 hover:bg-surface disabled:opacity-50"
              aria-label="Refresh summary"
              data-tooltip="Regenerate from the conversation, notes and attachments"
              disabled={busy}
              onClick={onRefresh}
            >
              {busy ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
            </button>
            <button
              type="button"
              className="rounded p-1.5 hover:bg-surface"
              aria-label="Edit summary"
              data-tooltip="Edit the summary"
              disabled={busy}
              onClick={() => {
                setDraft(summary.content);
                setDraftRevision(summary.revision);
                setEditing(true);
              }}
            >
              <Pencil size={14} />
            </button>
            <button
              type="button"
              className="rounded p-1.5 hover:bg-surface"
              aria-label="Delete summary"
              data-tooltip="Delete the summary"
              disabled={busy}
              onClick={() => void remove()}
            >
              <Trash2 size={14} />
            </button>
          </>
        )}
      </div>

      {error && (
        <div role="alert" className="mx-3 mb-2 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[12px] text-red-500">
          <span className="min-w-0 flex-1">{error}</span>
          <button type="button" aria-label="Dismiss error" onClick={onDismissError}>
            <X size={12} />
          </button>
        </div>
      )}

      {!collapsed && (
        <div className="max-h-[40vh] overflow-y-auto px-3 pb-3">
          {editing ? (
            <div className="space-y-2">
              <textarea
                ref={textareaRef}
                aria-label="Summary markdown"
                disabled={busy}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={10}
                className="w-full resize-y rounded border border-border bg-bg p-2 font-mono text-[12px] outline-none focus:border-accent/60"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-white disabled:opacity-50"
                  disabled={busy}
                  onClick={() => void save()}
                >
                  {busy ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                  Save
                </button>
                <button
                  type="button"
                  className="rounded border border-border px-2 py-1 text-[12px]"
                  disabled={busy}
                  onClick={() => {
                    setDraft(summary.content);
                    setEditing(false);
                  }}
                >
                  Cancel
                </button>
                <span className="text-[11px] text-muted">
                  Saved edits are preserved when the summary is refreshed.
                </span>
              </div>
            </div>
          ) : summary.content.trim() ? (
            <Markdown className="summary-markdown text-[13px]">{summary.content}</Markdown>
          ) : (
            <p className="text-[12px] text-muted">
              No summary yet — refresh to generate one, or add items with{" "}
              <code>/todo-summary</code> and <code>/important-summary</code>.
            </p>
          )}

          {suggestion && !editing && (
            <SuggestionReview
              key={summary.revision}
              hunks={hunks}
              busy={busy}
              onResolve={(indices) => void onResolve(indices, summary.revision)}
            />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Per-change review of a model proposal, rendered like a code diff: removed
 * lines in red, added lines in green, each change accepted or refused on its
 * own. Nothing is written to the summary until "Apply" is pressed.
 */
function SuggestionReview({
  hunks,
  busy,
  onResolve,
}: {
  hunks: TopicSummaryHunk[];
  busy: boolean;
  onResolve: (accepted: number[]) => void;
}) {
  const [accepted, setAccepted] = useState(() => new Set(hunks.map((h) => h.index)));

  function toggleHunk(index: number): void {
    setAccepted((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  return (
    <div className="mt-3 rounded border border-accent/40">
      <div className="flex flex-wrap items-center gap-2 border-b border-accent/30 bg-accent/10 px-2 py-1.5">
        <span className="min-w-0 flex-1 text-[12px]">
          The assistant suggests {hunks.length} change{hunks.length === 1 ? "" : "s"} to
          your summary.
        </span>
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-white disabled:opacity-50"
          disabled={busy}
          onClick={() => onResolve([...accepted])}
        >
          {busy ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
          Apply selected
        </button>
        <button
          type="button"
          className="rounded border border-border px-2 py-1 text-[12px]"
          disabled={busy}
          onClick={() => onResolve(hunks.map((h) => h.index))}
        >
          Accept all
        </button>
        <button
          type="button"
          className="rounded border border-border px-2 py-1 text-[12px]"
          disabled={busy}
          onClick={() => onResolve([])}
        >
          Refuse all
        </button>
      </div>
      <ul className="divide-y divide-border">
        {hunks.map((hunk) => {
          const isAccepted = accepted.has(hunk.index);
          return (
            <li key={hunk.index} className="flex items-start gap-2 px-2 py-1.5">
              <label className="flex shrink-0 items-center gap-1 pt-0.5 text-[11px] text-muted">
                <input
                  type="checkbox"
                  checked={isAccepted}
                  disabled={busy}
                  onChange={() => toggleHunk(hunk.index)}
                  aria-label={`Accept change ${hunk.index + 1}`}
                />
                Accept
              </label>
              <div className="min-w-0 flex-1 overflow-x-auto font-mono text-[11.5px] leading-snug">
                {hunk.removed.map((line, i) => (
                  <div
                    key={`r${i}`}
                    className={`whitespace-pre-wrap rounded px-1 ${
                      isAccepted
                        ? "bg-red-500/10 text-red-500 line-through"
                        : "bg-surface text-muted"
                    }`}
                  >
                    - {line || " "}
                  </div>
                ))}
                {hunk.added.map((line, i) => (
                  <div
                    key={`a${i}`}
                    className={`whitespace-pre-wrap rounded px-1 ${
                      isAccepted
                        ? "bg-emerald-500/10 text-emerald-600"
                        : "bg-surface text-muted line-through"
                    }`}
                  >
                    + {line || " "}
                  </div>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
