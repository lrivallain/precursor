import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  GitPullRequest,
  MessagesSquare,
  Send,
  Tag,
  X,
} from "lucide-react";
import type { IssueDetail, IssueLabel, ProjectCard } from "../lib/types";
import { api, apiErrorMessage } from "../lib/api";
import { useResizableBox } from "../lib/useResizableBox";
import { Modal } from "./Modal";
import { Markdown } from "./Markdown";
import { RefineTextarea } from "./RefineTextarea";
import { IssueLabelChip, IssueStateBadge } from "./IssueTags";

interface IssuePreviewModalProps {
  card: ProjectCard;
  /** Board repo, used when the card doesn't carry its own source repo. */
  fallbackRepo: string;
  onClose: () => void;
  /** Open the linked Precursor topic (when the issue has one). */
  onOpenTopic?: (topicId: number) => void;
  /** Notify the board that this issue's labels changed (to refresh cards). */
  onLabelsChanged?: (itemId: string, labels: IssueLabel[]) => void;
}

/**
 * Preview of a kanban card's issue/PR: title, state, labels, body, and
 * comments. Supports editing labels (from the repo's label set) and posting a
 * new comment. Also surfaces "Open on GitHub" and, when a Precursor topic is
 * linked to the issue, a shortcut to open that topic.
 */
export function IssuePreviewModal({
  card,
  fallbackRepo,
  onClose,
  onOpenTopic,
  onLabelsChanged,
}: IssuePreviewModalProps) {
  const repo = card.repo ?? fallbackRepo;
  const [detail, setDetail] = useState<IssueDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [commentText, setCommentText] = useState("");
  const [posting, setPosting] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);

  const [labelEditorOpen, setLabelEditorOpen] = useState(false);

  const { size, onResizeStart } = useResizableBox({
    storageKey: "precursor:issuePreview:size",
    defaultWidth: 672, // matches the previous max-w-2xl
    defaultHeight: Math.round(
      (typeof window !== "undefined" ? window.innerHeight : 800) * 0.85,
    ),
    minWidth: 380,
    minHeight: 320,
  });

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    if (card.number == null) {
      setError("This item has no issue number to preview.");
      return;
    }
    api.github
      .getIssue(card.number, repo)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e) => {
        if (!cancelled) setError(apiErrorMessage(e, "Failed to load issue"));
      });
    return () => {
      cancelled = true;
    };
  }, [card.number, repo]);

  const isPr = card.type === "pull_request";
  const stateForBadge = (detail?.state ?? card.state ?? "").toLowerCase();
  const labels = detail?.labels ?? card.labels;

  async function submitComment(): Promise<void> {
    const body = commentText.trim();
    if (!body || card.number == null) return;
    setPosting(true);
    setCommentError(null);
    try {
      const comment = await api.github.addIssueComment(card.number, body, repo);
      setDetail((d) => (d ? { ...d, comments: [...d.comments, comment] } : d));
      setCommentText("");
    } catch (e) {
      setCommentError(apiErrorMessage(e, "Failed to post comment"));
    } finally {
      setPosting(false);
    }
  }

  function applyLabels(next: IssueLabel[]): void {
    setDetail((d) => (d ? { ...d, labels: next } : d));
    onLabelsChanged?.(card.id, next);
  }

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      panelClassName="relative flex max-h-full max-w-full flex-col overflow-hidden rounded-xl border border-border bg-bg shadow-xl"
      panelStyle={{ width: size.width, height: size.height }}
    >
      <header className="flex items-start gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2 text-xs text-muted">
            {isPr && <GitPullRequest size={13} className="shrink-0" />}
            {card.number != null && (
              <span className="font-medium">
                {isPr ? "PR " : ""}#{card.number}
              </span>
            )}
            {stateForBadge && <IssueStateBadge state={stateForBadge} />}
            <span className="truncate">{repo}</span>
          </div>
          <h2 className="text-base font-semibold leading-snug">
            {detail?.title ?? card.title}
          </h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded p-1.5 text-muted hover:bg-surface hover:text-text"
          aria-label="Close preview"
        >
          <X size={16} />
        </button>
      </header>

      {/* Labels bar — stays fixed above the scroll area so the editor can
          float over the content instead of pushing it down. */}
      <div className="relative border-b border-border px-4 py-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {labels.map((label) => (
            <IssueLabelChip key={label.name} label={label} />
          ))}
          {card.number != null && (
            <button
              type="button"
              onClick={() => setLabelEditorOpen((v) => !v)}
              className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted hover:border-accent/50 hover:text-text"
            >
              <Tag size={11} />
              {labels.length ? "Edit labels" : "Add labels"}
            </button>
          )}
        </div>

        {labelEditorOpen && card.number != null && (
          <div className="absolute left-4 top-full z-20 mt-1 w-72 max-w-[calc(100%-2rem)]">
            <LabelEditor
              repo={repo}
              issueNumber={card.number}
              current={labels}
              onClose={() => setLabelEditorOpen(false)}
              onSaved={(next) => {
                applyLabels(next);
                setLabelEditorOpen(false);
              }}
            />
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {error ? (
          <div className="flex items-center gap-2 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
            <AlertTriangle size={14} className="shrink-0" />
            <span>{error}</span>
          </div>
        ) : detail === null ? (
          <div className="py-8 text-center text-sm text-muted">Loading…</div>
        ) : (
          <>
            {detail.body.trim() ? (
              <div className="rounded-lg border border-border bg-surface p-3.5 shadow-sm">
                <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  Description
                </div>
                <Markdown className="text-sm">{detail.body}</Markdown>
              </div>
            ) : (
              <p className="text-sm italic text-muted">No description provided.</p>
            )}

            {detail.comments.length > 0 && (
              <div className="mt-6 space-y-3">
                <div className="flex items-center gap-2">
                  <div className="h-px flex-1 bg-border" />
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-accent">
                    {detail.comments.length}{" "}
                    {detail.comments.length === 1 ? "comment" : "comments"}
                  </h3>
                  <div className="h-px flex-1 bg-border" />
                </div>
                {detail.comments.map((c) => (
                  <div
                    key={c.id}
                    className="ml-3 rounded-lg border border-accent/30 bg-accent/10 p-3 shadow-sm"
                  >
                    <div className="mb-1.5 flex items-center gap-2">
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent text-[10px] font-bold uppercase text-white">
                        {c.user.charAt(0)}
                      </span>
                      <span className="text-xs font-semibold text-text">@{c.user}</span>
                    </div>
                    <Markdown className="text-sm">{c.body}</Markdown>
                  </div>
                ))}
              </div>
            )}

            {/* Comment composer */}
            <div className="mt-5 border-t border-border pt-4">
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted">
                Add a comment
              </label>
              <RefineTextarea
                value={commentText}
                onValueChange={setCommentText}
                refineKind="comment"
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                    e.preventDefault();
                    void submitComment();
                  }
                }}
                rows={3}
                placeholder="Leave a comment… (⌘/Ctrl+Enter to post)"
                className="w-full resize-y rounded border border-border bg-surface px-2.5 py-2 text-sm outline-none focus:border-accent"
              />
              {commentError && (
                <p className="mt-1 text-xs text-red-500">{commentError}</p>
              )}
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  onClick={() => void submitComment()}
                  disabled={posting || !commentText.trim()}
                  className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Send size={14} />
                  {posting ? "Posting…" : "Comment"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      <footer className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
        <div>
          {detail?.linked_topic_id != null && onOpenTopic && (
            <button
              type="button"
              onClick={() => {
                onOpenTopic(detail.linked_topic_id!);
                onClose();
              }}
              className="inline-flex items-center gap-1.5 rounded-full border border-violet-500/40 bg-violet-500/10 px-3 py-1.5 text-sm font-medium text-violet-600 hover:bg-violet-500/20 dark:text-violet-300"
              title="Open the linked Precursor topic"
            >
              <MessagesSquare size={14} />
              <span className="max-w-[16rem] truncate">
                {detail.linked_topic_title ?? "Open topic"}
              </span>
            </button>
          )}
        </div>
        {(detail?.url ?? card.url) && (
          <a
            href={(detail?.url ?? card.url)!}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-sm hover:bg-surface"
          >
            <ExternalLink size={14} />
            Open on GitHub
          </a>
        )}
      </footer>

      {/* Bottom-right corner grip to resize the dialog (width + height). */}
      <div
        role="separator"
        aria-orientation="horizontal"
        onMouseDown={onResizeStart}
        title="Drag to resize"
        className="group absolute bottom-0 right-0 z-10 flex h-4 w-4 cursor-nwse-resize items-end justify-end p-0.5"
      >
        <svg viewBox="0 0 10 10" className="h-2.5 w-2.5 text-muted group-hover:text-accent">
          <path
            d="M9 1v8H1"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            opacity="0.5"
          />
          <path
            d="M9 5v4H5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </div>
    </Modal>
  );
}

interface LabelEditorProps {
  repo: string;
  issueNumber: number;
  current: IssueLabel[];
  onClose: () => void;
  onSaved: (labels: IssueLabel[]) => void;
}

/**
 * Inline multi-select of the repo's labels. Loads the repo label set on mount,
 * seeds the selection from the issue's current labels, and PUTs the new set on
 * save (GitHub replaces all labels).
 */
function LabelEditor({ repo, issueNumber, current, onClose, onSaved }: LabelEditorProps) {
  const [all, setAll] = useState<IssueLabel[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(current.map((l) => l.name)),
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    api.github
      .listLabels(repo)
      .then((list) => {
        if (!cancelled) setAll(list);
      })
      .catch((e) => {
        if (!cancelled) setError(apiErrorMessage(e, "Failed to load labels"));
      });
    return () => {
      cancelled = true;
    };
  }, [repo]);

  const filtered = useMemo(() => {
    const list = all ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((l) => l.name.toLowerCase().includes(q));
  }, [all, query]);

  function toggle(name: string): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      const next = await api.github.setIssueLabels(issueNumber, [...selected], repo);
      onSaved(next);
    } catch (e) {
      setError(apiErrorMessage(e, "Failed to save labels"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-bg p-3 shadow-xl">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          Labels
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-muted hover:bg-surface hover:text-text"
          aria-label="Cancel"
        >
          <X size={14} />
        </button>
      </div>

      {error && <p className="mb-2 text-xs text-red-500">{error}</p>}

      {all === null ? (
        <div className="py-3 text-center text-sm text-muted">Loading labels…</div>
      ) : (
        <>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter labels…"
            className="mb-2 w-full rounded border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
          />
          <div className="max-h-44 space-y-0.5 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="py-2 text-center text-xs text-muted">No labels.</div>
            ) : (
              filtered.map((label) => {
                const on = selected.has(label.name);
                return (
                  <button
                    key={label.name}
                    type="button"
                    onClick={() => toggle(label.name)}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm ${
                      on ? "bg-accent/10" : "hover:bg-surface"
                    }`}
                  >
                    <span
                      className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                        on ? "border-accent bg-accent text-white" : "border-border"
                      }`}
                    >
                      {on && <Check size={11} />}
                    </span>
                    <span
                      className="h-3 w-3 shrink-0 rounded-full"
                      style={{ backgroundColor: `#${label.color}` }}
                    />
                    <span className="truncate">{label.name}</span>
                  </button>
                );
              })
            )}
          </div>
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-border px-3 py-1.5 text-sm hover:bg-surface"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save labels"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
