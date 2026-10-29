import { useEffect, useState } from "react";
import { ApprovalCards } from "./ApprovalCards";
import { CompactionMarker } from "./CompactContext";
import { MessageBubble } from "./MessageBubble";
import { SideChatLinks } from "./SideChatsSection";
import { ReasoningDisclosure } from "./ReasoningDisclosure";
import { ReplyEditor } from "./ReplyEditor";
import { SuggestedReplies } from "./SuggestedReplies";
import { ToolCallBubble } from "./ToolCallBubble";
import { stripSuggestionBlock } from "../lib/suggestions";
import { parseToolMeta } from "../lib/toolMeta";
import type { MessageDeletion, PendingDelete } from "../lib/useMessageDeletion";
import { TURN_ANCHOR_ATTR } from "../lib/useTurnTimeline";
import type { Message, SideChatItem, ToolApproval, ToolApprovalDecision } from "../lib/types";
import { ArtifactCard, splitArtifactReply } from "./steps/ArtifactCard";
import { StepPlanTrace } from "./steps/StepPlanTrace";
import type { PromptRewindActions } from "../lib/useConversation";
import type { ReplyEditController } from "../lib/useReplyEdit";

interface TranscriptMessageProps {
  message: Message;
  streaming: boolean;
  /** Offer Retry on this prompt (its turn ended in an error). */
  retryable: boolean;
  onRetry: (message: Message) => void;
  onDelete: (message: Message) => void;
  collapsible?: boolean;
  /** Hide the per-message agent badge when an enclosing group already shows one. */
  hideAgentBadge?: boolean;
  /** Above the latest compaction marker: kept for the reader, hidden from the model. */
  compacted?: boolean;
  /** A rewind being previewed would delete this row. */
  doomed?: boolean;
  /** First row a previewed rewind deletes: draw the cut line above it. */
  cutAbove?: boolean;
  /** Prompt of the turn the timeline marks as current. */
  current?: boolean;
  /** Rewind actions for a prompt that opens a turn (see useConversation.rewindActions). */
  rewindActions?: PromptRewindActions;
  /** Show the prompt's action toolbar without hovering. */
  revealActions?: boolean;
  /** Topic transcripts: start a side chat from an assistant reply. */
  onStartSideChat?: (message: Message) => void;
  /** Side chats started from this reply, linked under it. */
  sideChats?: SideChatItem[];
  /** In-place editing of assistant replies (also enables selection actions). */
  replyEdit?: ReplyEditController;
}

/** One persisted-conversation row: a tool call, or a user/assistant/system bubble. */
export function TranscriptMessage(props: TranscriptMessageProps) {
  const { message: m, compacted, doomed, cutAbove } = props;
  const row = renderRow(props);
  if (row === null) return null;
  // A turn's prompt anchors the timeline rail (see useTurnTimeline).
  const anchor = m.role === "user" && !m.kind && m.id > 0;
  if (!anchor && !compacted && !doomed && !cutAbove) return row;
  const dim = doomed
    ? "opacity-35 grayscale transition-opacity"
    : compacted
      ? "opacity-55 transition-opacity hover:opacity-100"
      : undefined;
  return (
    <>
      {cutAbove && <RewindCutLine />}
      <div
        className={dim}
        data-compacted={compacted && !doomed ? "" : undefined}
        {...(anchor ? { [TURN_ANCHOR_ATTR]: m.id } : {})}
      >
        {row}
      </div>
    </>
  );
}

export function RewindCutLine() {
  return (
    <div
      className="flex items-center gap-3 text-[11px] font-semibold text-amber-700 dark:text-amber-300"
      role="separator"
      aria-label="The conversation restarts here"
    >
      <span className="flex-1 border-t-2 border-dashed border-amber-500/70" />
      conversation restarts here
      <span className="flex-1 border-t-2 border-dashed border-amber-500/70" />
    </div>
  );
}

function renderRow({
  message: m,
  streaming,
  retryable,
  onRetry,
  onDelete,
  collapsible,
  hideAgentBadge,
  current,
  rewindActions,
  revealActions,
  onStartSideChat,
  sideChats,
  replyEdit,
}: TranscriptMessageProps) {
  if (m.kind === "compaction") {
    return (
      <CompactionMarker
        message={m}
        onUndo={!streaming && m.id > 0 ? () => onDelete(m) : undefined}
      />
    );
  }
  if (m.kind === "step_plan") return <StepPlanTrace content={m.content} />;
  if (m.kind === "live_question") {
    // The question the Live assistant answered unattended; the suggestion follows.
    return (
      <div className="px-3 pt-1 text-[12px] text-muted">
        <span className="font-medium">Question heard</span>
        <span className="mx-1">·</span>
        <span className="italic">{m.content}</span>
      </div>
    );
  }
  if (m.role === "tool") {
    const meta = parseToolMeta(m.tool_calls);
    return (
      <ToolCallBubble
        name={meta?.name ?? "(unknown)"}
        arguments={meta?.arguments ?? "{}"}
        content={meta?.pending ? null : m.content}
        isError={Boolean(meta?.is_error)}
        pending={Boolean(meta?.pending)}
        stopped={Boolean(meta?.stopped)}
        link={meta?.link}
      />
    );
  }
  // Hide assistant turns that only emitted tool calls (no text): the tool
  // bubbles below carry the meaningful content. What the model thought before
  // reaching for them still gets its collapsed row.
  if (m.role === "assistant" && !m.content.trim() && m.tool_calls) {
    const thinking = m.reasoning?.trim();
    return thinking ? <ReasoningDisclosure reasoning={thinking} className="px-3" /> : null;
  }
  const canDelete =
    !streaming && m.id > 0 && (m.role === "user" || m.role === "assistant");
  const reply = m.role === "assistant" && m.id > 0 && !m.kind && !m.is_error;
  const sideChatSource = Boolean(onStartSideChat) && reply;
  const editable = Boolean(replyEdit) && reply && !streaming;
  const editing = replyEdit && replyEdit.editingId === m.id;
  // A deliverable marked by the model in a plan discussion is laid out as a card
  // rather than as raw marker comments.
  const split = m.role === "assistant" && !m.kind && !m.is_error ? splitArtifactReply(m.content) : null;
  const card = split ? (
    <ArtifactCard messageId={m.id} artifact={split.artifact} complete={split.complete} />
  ) : undefined;
  return (
    <MessageBubble
      role={m.role}
      content={split ? split.text : m.content}
      reasoning={m.reasoning}
      attachments={m.attachments}
      collapsible={collapsible}
      agentSessionId={hideAgentBadge ? undefined : m.agent_session_id}
      createdAt={m.created_at}
      model={m.model}
      provider={m.provider}
      elapsedMs={m.elapsed_ms}
      isError={m.is_error}
      onRetry={retryable ? () => onRetry(m) : undefined}
      onDelete={canDelete ? () => onDelete(m) : undefined}
      highlighted={current}
      onRewindHere={rewindActions?.here}
      onEditResend={rewindActions?.edit}
      revealActions={revealActions && Boolean(rewindActions)}
      onStartSideChat={sideChatSource && !split ? () => onStartSideChat?.(m) : undefined}
      quotableId={!split && (sideChatSource || (replyEdit && reply)) ? m.id : undefined}
      onEditReply={editable && !editing && !split ? () => replyEdit?.start(m.id) : undefined}
      editor={
        editing ? (
          <ReplyEditor
            content={m.content}
            selection={replyEdit.selection}
            saving={replyEdit.saving}
            error={replyEdit.error}
            onSave={(content) => void replyEdit.save(content)}
            onCancel={replyEdit.cancel}
          />
        ) : undefined
      }
      edited={
        reply && m.original_content != null
          ? {
              at: m.edited_at,
              original: m.original_content,
              onRestore: editable ? () => void replyEdit?.restoreOriginal(m) : undefined,
            }
          : undefined
      }
      footer={
        card || (sideChats && sideChats.length > 0) ? (
          <>
            {card}
            {sideChats && sideChats.length > 0 && <SideChatLinks items={sideChats} />}
          </>
        ) : undefined
      }
    />
  );
}

interface TranscriptTailProps {
  visibleMessages: Message[];
  streaming: boolean;
  pendingContent: string;
  pendingReasoning: string;
  onPickSuggestion: (text: string) => void;
  onStop: () => void;
  /** Tool calls the turn is parked on. */
  approvals?: ToolApproval[];
  onDecideApproval?: (approval: ToolApproval, decision: ToolApprovalDecision) => Promise<void>;
}

/** The live reply while streaming, otherwise the last answer's suggestion chips. */
export function TranscriptTail({
  visibleMessages,
  streaming,
  pendingContent,
  pendingReasoning,
  onPickSuggestion,
  onStop,
  approvals,
  onDecideApproval,
}: TranscriptTailProps) {
  if (streaming) {
    return (
      <MessageBubble
        role="assistant"
        content={stripSuggestionBlock(pendingContent)}
        reasoning={pendingReasoning}
        pending
        onStop={onStop}
      />
    );
  }
  if (approvals && approvals.length > 0 && onDecideApproval) {
    return <ApprovalCards approvals={approvals} onDecide={onDecideApproval} />;
  }
  const last = visibleMessages[visibleMessages.length - 1];
  if (last?.role === "assistant" && last.suggestions?.length) {
    return <SuggestedReplies items={last.suggestions} onPick={onPickSuggestion} />;
  }
  return null;
}

function removedLabel(message: Message): string {
  if (message.kind === "compaction") return "Compaction summary";
  if (message.role === "user") return "Your message";
  return message.role === "assistant" ? "Assistant reply" : "Message";
}

/** One undo row per message awaiting its delete-grace timeout. */
export function UndoDeleteToasts({
  deletion,
}: {
  deletion: Pick<MessageDeletion, "pendingDeletes" | "undoDelete">;
}) {
  const latest = deletion.pendingDeletes[deletion.pendingDeletes.length - 1];
  return (
    <>
      {/* Always mounted: screen readers skip a live region inserted along with
          its text. Out of flow, so it adds no gap to the composer stack. */}
      <span className="sr-only" role="status">
        {latest ? `${removedLabel(latest.message)} removed. Undo is available for a few seconds.` : ""}
      </span>
      {deletion.pendingDeletes.length > 0 && (
        <div className="flex flex-col gap-1">
          {deletion.pendingDeletes.map((p) => (
            <UndoDeleteToast
              key={p.message.id}
              pending={p}
              onUndo={() => deletion.undoDelete(p.message.id)}
            />
          ))}
        </div>
      )}
    </>
  );
}

function UndoDeleteToast({ pending, onUndo }: { pending: PendingDelete; onUndo: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(handle);
  }, []);
  const seconds = Math.max(0, Math.ceil((pending.expiresAt - now) / 1000));
  const label = removedLabel(pending.message);
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-1.5 rounded border border-border bg-surface text-xs">
      <span className="text-muted truncate">
        {label} removed · undo in {seconds}s
      </span>
      <button
        type="button"
        onClick={onUndo}
        className="text-accent hover:underline shrink-0"
        aria-label={`Undo: restore ${label.toLowerCase()}`}
      >
        Undo
      </button>
    </div>
  );
}
