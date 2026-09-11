import { useCallback, useEffect, useRef, useState } from "react";
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
import { api } from "../lib/api";
import { Markdown } from "./Markdown";
import type { TopicSummary, TopicSummaryHunk } from "../lib/types";

interface Props {
  topicId: number;
  summary: TopicSummary;
  busy: boolean;
  error: string | null;
  onChanged: (summary: TopicSummary | null) => void;
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
  topicId,
  summary,
  busy,
  error,
  onChanged,
  onRefresh,
  onToggleVisible,
  onDismissError,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(summary.content);
  const [saving, setSaving] = useState(false);
  const [accepted, setAccepted] = useState<Set<number>>(new Set());
  const [resolving, setResolving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const suggestion = summary.suggestion;
  const hunks = suggestion?.hunks ?? [];

  // A fresh proposal starts with every change accepted: the common case is
  // "yes, take the update", and refusing one is a single click.
  useEffect(() => {
    setAccepted(new Set(hunks.map((h) => h.index)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestion?.generated_at, hunks.length]);

  useEffect(() => {
    if (!editing) setDraft(summary.content);
  }, [summary.content, editing]);

  useEffect(() => {
    if (editing) textareaRef.current?.focus();
  }, [editing]);

  const save = useCallback(async (): Promise<void> => {
    setSaving(true);
    try {
      onChanged(await api.topicSummary.save(topicId, draft));
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }, [draft, onChanged, topicId]);

  async function resolve(all: "accept" | "refuse" | null): Promise<void> {
    const indices =
      all === "accept"
        ? hunks.map((h) => h.index)
        : all === "refuse"
          ? []
          : [...accepted];
    setResolving(true);
    try {
      onChanged(await api.topicSummary.resolve(topicId, indices));
    } finally {
      setResolving(false);
    }
  }

  async function remove(): Promise<void> {
    await api.topicSummary.remove(topicId);
    onChanged(null);
  }

  function toggleHunk(hunk: TopicSummaryHunk): void {
    setAccepted((prev) => {
      const next = new Set(prev);
      if (next.has(hunk.index)) next.delete(hunk.index);
      else next.add(hunk.index);
      return next;
    });
  }

  const collapsed = !summary.visible;

  return (
    <div className="border-b border-border bg-surface/40">
      <div className="flex items-center gap-1.5 px-3 py-1.5">
        <button
          type="button"
          onClick={onToggleVisible}
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
              onClick={() => setEditing(true)}
            >
              <Pencil size={14} />
            </button>
            <button
              type="button"
              className="rounded p-1.5 hover:bg-surface"
              aria-label="Delete summary"
              data-tooltip="Delete the summary"
              onClick={() => void remove()}
            >
              <Trash2 size={14} />
            </button>
          </>
        )}
      </div>

      {error && (
        <div className="mx-3 mb-2 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[12px] text-red-500">
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
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={10}
                className="w-full resize-y rounded border border-border bg-bg p-2 font-mono text-[12px] outline-none focus:border-accent/60"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-white disabled:opacity-50"
                  disabled={saving}
                  onClick={() => void save()}
                >
                  {saving ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                  Save
                </button>
                <button
                  type="button"
                  className="rounded border border-border px-2 py-1 text-[12px]"
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
              hunks={hunks}
              accepted={accepted}
              busy={resolving}
              onToggle={toggleHunk}
              onApply={() => void resolve(null)}
              onAcceptAll={() => void resolve("accept")}
              onRefuseAll={() => void resolve("refuse")}
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
  accepted,
  busy,
  onToggle,
  onApply,
  onAcceptAll,
  onRefuseAll,
}: {
  hunks: TopicSummaryHunk[];
  accepted: Set<number>;
  busy: boolean;
  onToggle: (hunk: TopicSummaryHunk) => void;
  onApply: () => void;
  onAcceptAll: () => void;
  onRefuseAll: () => void;
}) {
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
          onClick={onApply}
        >
          {busy ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
          Apply selected
        </button>
        <button
          type="button"
          className="rounded border border-border px-2 py-1 text-[12px]"
          disabled={busy}
          onClick={onAcceptAll}
        >
          Accept all
        </button>
        <button
          type="button"
          className="rounded border border-border px-2 py-1 text-[12px]"
          disabled={busy}
          onClick={onRefuseAll}
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
                  onChange={() => onToggle(hunk)}
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
