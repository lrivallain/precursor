import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ExternalLink,
  GitPullRequest,
  MessagesSquare,
  X,
} from "lucide-react";
import type { IssueDetail, ProjectCard } from "../lib/types";
import { api, apiErrorMessage } from "../lib/api";
import { Modal } from "./Modal";
import { Markdown } from "./Markdown";
import { IssueLabelChip, IssueStateBadge } from "./IssueTags";

interface IssuePreviewModalProps {
  card: ProjectCard;
  /** Board repo, used when the card doesn't carry its own source repo. */
  fallbackRepo: string;
  onClose: () => void;
  /** Open the linked Precursor topic (when the issue has one). */
  onOpenTopic?: (topicId: number) => void;
}

/**
 * Read-only preview of a kanban card's issue/PR: title, state, labels, body,
 * and comments, fetched on open. Surfaces "Open on GitHub" and, when a
 * Precursor topic is linked to the issue, a shortcut to open that topic.
 */
export function IssuePreviewModal({
  card,
  fallbackRepo,
  onClose,
  onOpenTopic,
}: IssuePreviewModalProps) {
  const [detail, setDetail] = useState<IssueDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    if (card.number == null) {
      setError("This item has no issue number to preview.");
      return;
    }
    api.github
      .getIssue(card.number, card.repo ?? fallbackRepo)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e) => {
        if (!cancelled) setError(apiErrorMessage(e, "Failed to load issue"));
      });
    return () => {
      cancelled = true;
    };
  }, [card.number, card.repo, fallbackRepo]);

  const isPr = card.type === "pull_request";
  const stateForBadge = (detail?.state ?? card.state ?? "").toLowerCase();

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      panelClassName="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-bg shadow-xl"
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
            <span className="truncate">{card.repo ?? fallbackRepo}</span>
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

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {(detail?.labels?.length ?? card.labels.length) > 0 && (
          <div className="mb-3 flex flex-wrap gap-1">
            {(detail?.labels ?? card.labels).map((label) => (
              <IssueLabelChip key={label.name} label={label} />
            ))}
          </div>
        )}

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
              <Markdown className="text-sm">{detail.body}</Markdown>
            ) : (
              <p className="text-sm italic text-muted">No description provided.</p>
            )}

            {detail.comments.length > 0 && (
              <div className="mt-5 space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
                  {detail.comments.length}{" "}
                  {detail.comments.length === 1 ? "comment" : "comments"}
                </h3>
                {detail.comments.map((c) => (
                  <div key={c.id} className="rounded-lg border border-border bg-surface/40 p-3">
                    <div className="mb-1 text-xs font-medium text-muted">
                      @{c.user}
                    </div>
                    <Markdown className="text-sm">{c.body}</Markdown>
                  </div>
                ))}
              </div>
            )}
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
    </Modal>
  );
}
