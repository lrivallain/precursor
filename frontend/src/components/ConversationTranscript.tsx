import { MessageBubble } from "./MessageBubble";
import { SuggestedReplies } from "./SuggestedReplies";
import { ToolCallBubble } from "./ToolCallBubble";
import { stripSuggestionBlock } from "../lib/suggestions";
import { parseToolMeta } from "../lib/toolMeta";
import type { Message } from "../lib/types";

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
}

/** One persisted-conversation row: a tool call, or a user/assistant/system bubble. */
export function TranscriptMessage({
  message: m,
  streaming,
  retryable,
  onRetry,
  onDelete,
  collapsible,
  hideAgentBadge,
}: TranscriptMessageProps) {
  if (m.role === "tool") {
    const meta = parseToolMeta(m.tool_calls);
    return (
      <ToolCallBubble
        name={meta?.name ?? "(unknown)"}
        arguments={meta?.arguments ?? "{}"}
        content={meta?.pending ? null : m.content}
        isError={Boolean(meta?.is_error)}
        pending={Boolean(meta?.pending)}
        link={meta?.link}
      />
    );
  }
  // Hide assistant turns that only emitted tool calls (no text):
  // the tool bubbles below carry the meaningful content.
  if (m.role === "assistant" && !m.content.trim() && m.tool_calls) {
    return null;
  }
  const canDelete =
    !streaming && m.id > 0 && (m.role === "user" || m.role === "assistant");
  return (
    <MessageBubble
      role={m.role}
      content={m.content}
      attachments={m.attachments}
      collapsible={collapsible}
      agentSessionId={hideAgentBadge ? undefined : m.agent_session_id}
      createdAt={m.created_at}
      model={m.model}
      elapsedMs={m.elapsed_ms}
      isError={m.is_error}
      onRetry={retryable ? () => onRetry(m) : undefined}
      onDelete={canDelete ? () => onDelete(m) : undefined}
    />
  );
}

interface TranscriptTailProps {
  visibleMessages: Message[];
  streaming: boolean;
  pendingContent: string;
  onPickSuggestion: (text: string) => void;
  onStop: () => void;
}

/** The live reply while streaming, otherwise the last answer's suggestion chips. */
export function TranscriptTail({
  visibleMessages,
  streaming,
  pendingContent,
  onPickSuggestion,
  onStop,
}: TranscriptTailProps) {
  if (streaming) {
    return (
      <MessageBubble
        role="assistant"
        content={stripSuggestionBlock(pendingContent)}
        pending
        onStop={onStop}
      />
    );
  }
  const last = visibleMessages[visibleMessages.length - 1];
  if (last?.role === "assistant" && last.suggestions?.length) {
    return <SuggestedReplies items={last.suggestions} onPick={onPickSuggestion} />;
  }
  return null;
}
