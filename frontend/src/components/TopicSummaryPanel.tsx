import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
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
import { useResizableHeight } from "../lib/useResizableHeight";
import type { TopicSummary, TopicSummaryHunk } from "../lib/types";

interface Props {
  topicId: number;
  summary: TopicSummary | null;
  busy: boolean;
  error: string | null;
  refreshNotice: string | null;
  onSave: (content: string, revision: string) => Promise<TopicSummary | null>;
  onResolve: (accepted: number[], revision: string, reviewed?: number[]) => Promise<boolean>;
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
 *  - *review* — when a refresh lands on a user-edited brief the model's
 *    proposal is shown merged into the brief in place: each change appears at
 *    its real position with its own Accept / Reject, decided independently.
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
  const panelRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const editOffsetRef = useRef<number | null>(null);
  const confirm = useConfirm();

  const suggestion = summary?.suggestion;
  const hunks = suggestion?.hunks ?? [];
  const collapsed = !summary?.visible;
  const hasContent = Boolean(draft.content.trim());
  const [maxHeight, setMaxHeight] = useState(window.innerHeight);
  const minHeight = Math.min(120, maxHeight);
  const { height, onPointerDown, onKeyDown, cancelResize } = useResizableHeight({
    storageKey: "precursor:topic-summary:height",
    defaultHeight: Math.min(400, Math.round(window.innerHeight * 0.4)),
    min: minHeight,
    max: maxHeight,
    side: "bottom",
  });

  useLayoutEffect(() => {
    const panel = panelRef.current;
    const parent = panel?.parentElement;
    if (!panel || !parent) return;
    function measure(): void {
      if (!panel || !parent) return;
      const reserved = Array.from(parent.children).reduce((total, child) =>
        child !== panel && getComputedStyle(child).flexGrow === "0"
          ? total + child.getBoundingClientRect().height : total, 0);
      const chrome = panel.getBoundingClientRect().height
        - (bodyRef.current?.getBoundingClientRect().height ?? 0);
      // Keep the composer and some transcript visible, including after either
      // the window or the existing composer resize handle changes the layout.
      setMaxHeight(Math.max(64, Math.floor(parent.clientHeight - reserved - chrome - 80)));
    }
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    for (const child of parent.children) observer.observe(child);
    measure();
    return () => observer.disconnect();
  }, [collapsed]);

  useEffect(() => {
    if (collapsed) cancelResize();
  }, [collapsed, cancelResize]);
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
    <div ref={panelRef} data-summary-panel aria-busy={busy} className="shrink-0 border-b border-border bg-surface/40">
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
          <div
            ref={bodyRef}
            id={`${contentId}-body`}
            data-summary-scroll
            style={{ height }}
            className={`overflow-auto px-3 pb-3 ${editing ? "border-t border-border/60 pt-3" : ""}`}
          >
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
                  style={{ height: Math.max(48, height - 76) }}
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
            ) : suggestion && !draft.dirty && !draft.saving ? (
              <SuggestionDiff
                content={draft.content}
                hunks={hunks}
                busy={busy}
                onResolve={(indices, reviewed) => void onResolve(indices, summary.revision, reviewed)}
              />
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
          </div>
        )}
        <div
          role="separator"
          tabIndex={0}
          aria-label="Resize topic summary"
          aria-orientation="horizontal"
          aria-controls={`${contentId}-body`}
          aria-valuemin={minHeight}
          aria-valuemax={maxHeight}
          aria-valuenow={Math.round(height)}
          aria-valuetext={`${Math.round(height)} pixels`}
          data-tooltip="Drag to resize summary. Use ↑/↓ or Home/End with the keyboard."
          onPointerDown={onPointerDown}
          onKeyDown={onKeyDown}
          className="summary-action group flex h-3 w-full cursor-row-resize touch-none items-center justify-center border-t border-border/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
        >
          <span aria-hidden="true" className="h-0.5 w-10 rounded bg-muted group-hover:bg-accent group-focus-visible:bg-accent" />
        </div>
      </div>
    </div>
  );
}

/**
 * Segment of the merged inline view: a run of unchanged context lines, or one
 * reviewable hunk positioned at its real place in the document.
 */
type DiffSegment =
  | { kind: "context"; key: string; lines: string[] }
  | { kind: "hunk"; key: string; hunk: TopicSummaryHunk };

/**
 * Split ``content`` into ordered segments with each hunk woven in at its
 * ``base_start``/``base_end`` position, so the reviewer sees the change in
 * place rather than in a list detached from the rest of the brief.
 */
function diffSegments(content: string, hunks: TopicSummaryHunk[]): DiffSegment[] {
  const lines = content.split("\n");
  const segments: DiffSegment[] = [];
  let cursor = 0;
  for (const hunk of hunks) {
    if (hunk.base_start > cursor) {
      segments.push({
        kind: "context",
        key: `c${cursor}`,
        lines: lines.slice(cursor, hunk.base_start),
      });
    }
    segments.push({ kind: "hunk", key: `h${hunk.index}`, hunk });
    cursor = hunk.base_end;
  }
  if (cursor < lines.length) {
    segments.push({ kind: "context", key: `c${cursor}`, lines: lines.slice(cursor) });
  }
  return segments;
}

/**
 * The brief merged with its pending proposal: unchanged text reads normally,
 * and each hunk appears inline at its real position with its own Accept /
 * Reject pair. A decision saves immediately; the rest stay pending against
 * the updated brief (hunk indices are re-based server-side after every write).
 */
function SuggestionDiff({
  content,
  hunks,
  busy,
  onResolve,
}: {
  content: string;
  hunks: TopicSummaryHunk[];
  busy: boolean;
  onResolve: (accepted: number[], reviewed?: number[]) => void;
}) {
  const segments = useMemo(() => diffSegments(content, hunks), [content, hunks]);
  return (
    <div className="rounded border border-accent/40">
      <div className="flex flex-wrap items-center gap-2 border-b border-accent/30 bg-accent/10 px-2 py-1.5">
        <span className="min-w-0 flex-1 text-[12px]">
          The assistant suggests {hunks.length} change{hunks.length === 1 ? "" : "s"} to
          your summary, shown in place below.
        </span>
        <button
          type="button"
          className="summary-action rounded border border-border px-2 py-1 text-[12px] disabled:opacity-50"
          disabled={busy}
          onClick={() => onResolve(hunks.map((h) => h.index))}
        >
          Accept all
        </button>
        <button
          type="button"
          className="summary-action rounded border border-border px-2 py-1 text-[12px] disabled:opacity-50"
          disabled={busy}
          onClick={() => onResolve([])}
        >
          Reject all
        </button>
      </div>
      <div className="overflow-x-auto px-2 py-2 font-mono text-[11.5px] leading-snug">
        {segments.map((segment) =>
          segment.kind === "context" ? (
            <ContextLines key={segment.key} lines={segment.lines} />
          ) : (
            <HunkBlock key={segment.key} hunk={segment.hunk} busy={busy} onResolve={onResolve} />
          ),
        )}
      </div>
    </div>
  );
}

/** Unchanged lines rendered as-is, in the same monospace flow as the hunks. */
function ContextLines({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <>
      {lines.map((line, i) => (
        <div key={i} className="whitespace-pre-wrap px-1">
          {line || " "}
        </div>
      ))}
    </>
  );
}

/** One reviewable change: removed lines struck through, added lines below, each
 * with its own compact Accept / Reject pair. */
function HunkBlock({
  hunk,
  busy,
  onResolve,
}: {
  hunk: TopicSummaryHunk;
  busy: boolean;
  onResolve: (accepted: number[], reviewed?: number[]) => void;
}) {
  return (
    <div data-summary-change className="my-1 flex items-start gap-1.5 rounded bg-surface/70 p-1.5">
      <div className="min-w-0 flex-1 space-y-0.5">
        {hunk.removed.map((line, i) => (
          <div
            key={`r${i}`}
            className="whitespace-pre-wrap rounded bg-red-500/10 px-1 text-red-600 line-through dark:text-red-400"
          >
            - {line || " "}
          </div>
        ))}
        {hunk.added.map((line, i) => (
          <div
            key={`a${i}`}
            className="whitespace-pre-wrap rounded bg-emerald-500/10 px-1 text-emerald-700 dark:text-emerald-400"
          >
            + {line || " "}
          </div>
        ))}
      </div>
      <div className="inline-flex shrink-0 divide-x divide-border overflow-hidden rounded border border-border">
        <button
          type="button"
          aria-label={`Accept change ${hunk.index + 1}`}
          data-tooltip="Accept this change"
          className="summary-action flex size-6 items-center justify-center text-emerald-700 hover:bg-surface focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent disabled:opacity-50 dark:text-emerald-400"
          disabled={busy}
          onClick={() => onResolve([hunk.index], [hunk.index])}
        >
          <Check size={14} />
        </button>
        <button
          type="button"
          aria-label={`Reject change ${hunk.index + 1}`}
          data-tooltip="Reject this change"
          className="summary-action flex size-6 items-center justify-center text-muted hover:bg-surface hover:text-text focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
          disabled={busy}
          onClick={() => onResolve([], [hunk.index])}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
