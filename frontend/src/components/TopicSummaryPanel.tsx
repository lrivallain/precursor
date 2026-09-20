import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { useConfirm } from "./ConfirmDialog";
import { Markdown } from "./Markdown";
import { useTopicSummaryDraft } from "../lib/useTopicSummaryDraft";
import { focusTextareaAt } from "../lib/markdownCaret";
import type { TopicSummary, TopicSummaryHunk } from "../lib/types";

interface Props {
  topicId: number;
  summary: TopicSummary | null;
  busy: boolean;
  error: string | null;
  refreshNotice: string | null;
  onSave: (content: string, revision: string) => Promise<TopicSummary | null>;
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
 *  - *read* — rendered markdown with directly checkable actions;
 *  - *edit* — an autosaving textarea; manual changes mark the brief as
 *    user-owned so a later refresh can never silently overwrite it;
 *  - *review* — when a refresh lands on a user-edited brief the model's version
 *    arrives as a list of changes, each accepted or refused on its own before
 *    anything is written.
 */
export function TopicSummaryPanel({
  topicId,
  summary,
  busy,
  error,
  refreshNotice,
  onSave,
  onResolve,
  onRemove,
  onRefresh,
  onToggleVisible,
  onDismissError,
}: Props) {
  const [editing, setEditing] = useState(false);
  const draft = useTopicSummaryDraft(topicId, summary, onSave);
  const contentId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const editOffsetRef = useRef<number | null>(null);
  const confirm = useConfirm();

  const suggestion = summary?.suggestion;
  const hunks = suggestion?.hunks ?? [];
  const collapsed = !summary?.visible;
  const hasContent = Boolean(draft.content.trim());
  const savingStatus = (
    <span role="status" className="inline-flex items-center gap-1 text-[11px] text-muted">
      {draft.saving ? (
        <><Loader2 size={11} className="animate-spin" /> Saving…</>
      ) : draft.failed ? (
        "Not saved"
      ) : draft.dirty ? (
        "Unsaved changes"
      ) : refreshNotice && !busy && !error ? (
        <><Check size={11} /> {refreshNotice}</>
      ) : (
        <><Check size={11} /> Saved</>
      )}
    </span>
  );

  useEffect(() => {
    if (!summary) setEditing(false);
  }, [summary]);

  useLayoutEffect(() => {
    if (!editing || !textareaRef.current) return;
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
    if (editOffsetRef.current === null) textareaRef.current.focus({ preventScroll: true });
    else focusTextareaAt(textareaRef.current, editOffsetRef.current);
    editOffsetRef.current = null;
  }, [editing]);

  function beginEditing(offset: number | null = null): void {
    if (busy) return;
    editOffsetRef.current = offset;
    setEditing(true);
  }

  async function finishEditing(): Promise<void> {
    if (await draft.flush()) setEditing(false);
  }

  async function toggleVisible(): Promise<void> {
    if (!collapsed && !await draft.flush()) return;
    onToggleVisible();
  }

  async function reloadLatest(): Promise<void> {
    if (await confirm({
      title: "Reload saved summary?",
      message: "Discard your unsaved local changes and use the latest saved summary?",
      confirmLabel: "Reload latest",
      variant: "warning",
    })) {
      draft.discard();
      onDismissError();
    }
  }

  async function remove(): Promise<void> {
    if (!summary) return;
    if (await confirm({
      title: "Delete summary?",
      message: "The summary and any pending suggestions will be permanently deleted.",
      confirmLabel: "Delete",
      variant: "danger",
    })) {
      await onRemove(summary.revision);
    }
  }

  return (
    <div data-summary-panel aria-busy={busy} className="shrink-0 border-b border-border bg-surface/40">
      <button
        type="button"
        onClick={() => void toggleVisible()}
        disabled={busy}
        aria-label={collapsed ? "Expand topic summary" : "Collapse topic summary"}
        aria-expanded={!collapsed}
        aria-controls={contentId}
        aria-describedby={suggestion ? `${contentId}-changes` : undefined}
        className="summary-toggle relative flex min-h-8 w-full items-center justify-center text-muted hover:bg-surface hover:text-text focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent disabled:cursor-wait"
      >
        {busy ? (
          <span role="status">
            <Loader2 size={20} className="animate-spin motion-reduce:animate-none" />
            <span className="sr-only">Updating summary…</span>
          </span>
        ) : (
          <ChevronDown
            size={22}
            strokeWidth={1.25}
            className={`motion-safe:transition-transform motion-safe:duration-150 ${collapsed ? "" : "rotate-180"}`}
          />
        )}
        <span className="absolute left-1/2 ml-7 flex max-w-[calc(50%-2rem)] items-center gap-1.5 text-[10px] uppercase tracking-wide">
          <FileText size={11} className="shrink-0" />
          <span>Summary</span>
          {suggestion && (
            <span
              id={`${contentId}-changes`}
              className="rounded bg-accent/15 px-1.5 py-0.5 normal-case tracking-normal text-accent"
              data-tooltip={`${hunks.length} suggested change${hunks.length === 1 ? "" : "s"}`}
            >
              {hunks.length}
              <span className="sr-only"> suggested change{hunks.length === 1 ? "" : "s"}</span>
            </span>
          )}
        </span>
      </button>

      {error && (
        <div role="alert" className="mx-3 my-2 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[12px] text-red-500">
          <span className="min-w-0 flex-1">{error}</span>
          <button type="button" aria-label="Dismiss error" onClick={onDismissError}>
            <X size={12} />
          </button>
        </div>
      )}
      {draft.failed && (
        <div className="mx-3 my-2 flex flex-wrap items-center gap-2 text-[12px]">
          <span>Your changes are kept here but have not been saved.</span>
          <button
            type="button"
            className="summary-action rounded border border-border px-2 py-1"
            disabled={busy || draft.saving}
            onClick={() => void draft.flush()}
          >
            Retry save
          </button>
          <button
            type="button"
            className="summary-action rounded border border-border px-2 py-1"
            disabled={busy || draft.saving}
            onClick={() => void reloadLatest()}
          >
            Reload latest
          </button>
        </div>
      )}

      <div id={contentId} role="region" aria-label="Topic summary" hidden={collapsed}>
        {summary && !editing && (
          <div className="flex items-center justify-end gap-1 border-t border-border/60 px-3 py-1">
            <div className="mr-auto">{savingStatus}</div>
            {hasContent && (
              <button
                type="button"
                className="summary-action rounded p-1.5 hover:bg-surface disabled:opacity-50"
                aria-label="Refresh summary"
                data-tooltip="Regenerate from the conversation, notes and attachments"
                disabled={busy || draft.dirty || draft.saving}
                onClick={onRefresh}
              >
                <RefreshCw size={14} />
              </button>
            )}
            <button
              type="button"
              className="summary-action rounded p-1.5 hover:bg-surface disabled:opacity-50"
              aria-label="Edit summary"
              data-tooltip="Edit the summary (or double-click its text)"
              disabled={busy}
              onClick={() => beginEditing()}
            >
              <Pencil size={14} />
            </button>
            <button
              type="button"
              className="summary-action rounded p-1.5 hover:bg-surface disabled:opacity-50"
              aria-label="Delete summary"
              data-tooltip="Delete the summary"
              disabled={busy || draft.dirty || draft.saving}
              onClick={() => void remove()}
            >
              <Trash2 size={14} />
            </button>
          </div>
        )}

        {summary && (
          <div ref={bodyRef} className={`max-h-[40vh] overflow-y-auto px-3 pb-3 ${editing ? "border-t border-border/60 pt-3" : ""}`}>
            {editing ? (
              <div className="space-y-2">
                <textarea
                  ref={textareaRef}
                  aria-label="Summary markdown"
                  disabled={busy}
                  value={draft.content}
                  onChange={(e) => draft.change(e.target.value)}
                  onBlur={() => {
                    if (!draft.failed) void draft.flush();
                  }}
                  rows={10}
                  className="w-full resize-y rounded border border-border bg-bg p-2 font-mono text-[12px] outline-none focus:border-accent/60"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="summary-action inline-flex items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-bg disabled:opacity-50"
                    disabled={busy}
                    onClick={() => void finishEditing()}
                  >
                    <Check size={11} />
                    Done
                  </button>
                  {savingStatus}
                  <span className="text-[11px] text-muted">
                    Edits save automatically and are preserved on refresh.
                  </span>
                </div>
              </div>
            ) : hasContent ? (
              <Markdown
                className="summary-markdown text-[13px]"
                tasksDisabled={busy}
                onTextDoubleClick={busy ? undefined : beginEditing}
                onTaskChange={(content) => {
                  draft.change(content);
                  void draft.flush();
                }}
              >
                {draft.content}
              </Markdown>
            ) : (
              <div className="space-y-3 pb-1">
                <p className="text-[12px] text-muted">
                  No summary yet. Generate a brief from this topic, or add items with{" "}
                  <code>/todo-summary</code> and <code>/important-summary</code>.
                </p>
                <button
                  type="button"
                  className="summary-action inline-flex items-center gap-2 rounded bg-accent px-3 py-1.5 text-[12px] text-bg disabled:opacity-50"
                  disabled={busy}
                  onClick={onRefresh}
                >
                  <RefreshCw size={13} />
                  Generate summary
                </button>
              </div>
            )}

            {suggestion && !editing && !draft.dirty && !draft.saving && (
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
