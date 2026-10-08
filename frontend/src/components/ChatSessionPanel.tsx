import { useEffect, useMemo, useRef, useState } from "react";
import { CommandDraftCard } from "./CommandDraftCard";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { ConversationNotes, NotesConfirmModal } from "./ConversationNotes";
import { TranscriptMessage, TranscriptTail, UndoDeleteToasts } from "./ConversationTranscript";
import { ResizeHandle } from "./ResizeHandle";
import { RewindBar } from "./RewindBar";
import { SelectionActions } from "./SelectionActions";
import { TimelineRail } from "./TimelineRail";
import { api } from "../lib/api";
import { useSettings } from "../lib/settingsStore";
import { subscribeTopicNote } from "../lib/sideChats";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useComposerInput } from "../lib/useComposerInput";
import { useConversation } from "../lib/useConversation";
import { ReminderModal } from "./ReminderModal";
import { ReminderBanner } from "./ReminderBanner";
import type { Chat } from "../lib/types";
import { RoleSelector } from "./RoleSelector";
import { SideChatSeedCard } from "./SideChatSeedCard";

interface TopicNoteState {
  loading: boolean;
  posting: boolean;
  body: string;
  error: string | null;
  topicTitle: string | null;
}

interface ChatSessionPanelProps {
  chat: Chat;
  /** Refresh the chat list + active chat after a rename / pin / clear. */
  onChatUpdated: () => void;
  /** The chat was archived via /archive — drop the selection. */
  onArchived: () => void;
  /** Refresh the sidebar Reminders section after a set / cancel / done. */
  onRemindersChanged?: () => void;
  /** Persist a role change for this chat (null = default). */
  onSetRole?: (roleId: number | null) => Promise<void>;
}

// Chats are flat sessions with no GitHub issue, so the gh-* commands, the
// tree-only /new and the topic-only /agent don't apply: the "chat" command
// surface (see lib/commands.ts) leaves them out.
export function ChatSessionPanel({
  chat,
  onChatUpdated,
  onArchived,
  onRemindersChanged,
  onSetRole,
}: ChatSessionPanelProps) {
  const settings = useSettings();
  const showStats = settings?.show_chat_stats ?? true;

  // `/send-to-topic` only means something in a chat linked to a topic.
  const linked = chat.parent_topic_id != null;
  const excludedCommands = useMemo<ReadonlySet<string>>(
    () => new Set(linked ? [] : ["send-to-topic"]),
    [linked],
  );
  const composer = useComposerInput({ surface: "chat", exclude: excludedCommands });
  const [topicNote, setTopicNote] = useState<TopicNoteState | null>(null);
  const conv = useConversation({
    kind: "chat",
    id: chat.id,
    composer,
    onCommand: dispatchCommand,
    onUpdated: onChatUpdated,
    onRemindersChanged,
    onSetRole,
  });
  const { streamKey, systemNote, visibleMessages, streaming, reminders } = conv;
  const rewindFrom = conv.rewind.preview?.fromId ?? null;

  const { width: chatWidth, onMouseDown: onChatResize } = useResizableWidth({
    storageKey: "precursor:chat:width",
    defaultWidth: 768,
    min: 480,
    max: 1400,
  });
  const { height: composerHeight, onMouseDown: onComposerResize } =
    useResizableHeight({
      storageKey: "precursor:composer:height",
      defaultHeight: 56,
      min: 40,
      max: 480,
    });

  // Draft a note summing up this side chat for its topic, for review.
  async function startTopicNote(focus: string): Promise<void> {
    if (!linked) {
      systemNote("This chat isn't linked to a topic.");
      return;
    }
    setTopicNote({ loading: true, posting: false, body: "", error: null, topicTitle: null });
    try {
      const draft = await api.sideChats.draftTopicNote(chat.id, focus.trim() || undefined);
      setTopicNote((prev) =>
        prev ? { ...prev, loading: false, body: draft.text, topicTitle: draft.topic_title } : prev,
      );
    } catch (err) {
      setTopicNote((prev) =>
        prev ? { ...prev, loading: false, error: (err as Error).message } : prev,
      );
    }
  }

  async function sendTopicNote(body: string): Promise<void> {
    setTopicNote((prev) => (prev ? { ...prev, posting: true, error: null } : prev));
    try {
      await api.sideChats.sendTopicNote(chat.id, body);
      const where = topicNote?.topicTitle ?? chat.parent_topic_title ?? "the topic";
      setTopicNote(null);
      systemNote(`Sent a note to “${where}”.`);
    } catch (err) {
      setTopicNote((prev) =>
        prev ? { ...prev, posting: false, error: (err as Error).message } : prev,
      );
    }
  }

  // The header's "Send to topic" button asks through lib/sideChats.
  const startTopicNoteRef = useRef(startTopicNote);
  startTopicNoteRef.current = startTopicNote;
  useEffect(
    () => subscribeTopicNote(chat.id, () => void startTopicNoteRef.current("")),
    [chat.id],
  );

  async function dispatchCommand(name: string, argument: string): Promise<void> {
    if (name === "rename") {
      const title = argument.trim();
      if (!title) return systemNote("Usage: `/rename <new title>`");
      try {
        await api.chats.update(chat.id, { title });
        onChatUpdated();
      } catch (err) {
        systemNote(`Rename failed: ${(err as Error).message}`);
      }
      return;
    }
    if (name === "suggest-name") {
      try {
        const { title } = await api.chats.suggestName(chat.id);
        onChatUpdated();
        systemNote(
          title
            ? `Renamed this chat to "${title}".`
            : "Could not suggest a name; the title is unchanged.",
        );
      } catch (err) {
        systemNote(`Suggest name failed: ${(err as Error).message}`);
      }
      return;
    }
    if (name === "pin" || name === "unpin") {
      const pinned = name === "pin";
      if (chat.pinned === pinned) return systemNote(pinned ? "Already pinned." : "Not pinned.");
      try {
        await api.chats.update(chat.id, { pinned });
        onChatUpdated();
      } catch (err) {
        systemNote(`${pinned ? "Pin" : "Unpin"} failed: ${(err as Error).message}`);
      }
      return;
    }
    if (name === "send-to-topic") {
      await startTopicNote(argument);
      return;
    }
    if (name === "archive") {
      try {
        await api.chats.archive(chat.id);
        onArchived();
      } catch (err) {
        systemNote(`Archive failed: ${(err as Error).message}`);
      }
    }
  }

  return (
    <div className="h-full flex min-h-0">
      <div className="flex-1 flex flex-col min-h-0">
        {reminders.reminder && reminders.reminder.status === "fired" && (
          <ReminderBanner
            reminder={reminders.reminder}
            busy={reminders.reminderBusy}
            onDone={() => void reminders.runReminderClear(true)}
          />
        )}
        <div className="relative flex min-h-0 flex-1">
        <SelectionActions
          scrollRef={conv.scrollRef}
          sourceOf={(id) => conv.messages.find((m) => m.id === id)?.content}
          onEdit={streaming ? undefined : conv.replyEdit.start}
          onNote={(markdown) => void conv.notes.appendToPad(markdown)}
        />
        <div ref={conv.scrollRef} onScroll={conv.onScroll} className="flex-1 overflow-y-auto p-4 pr-12 min-w-0">
          <div className="relative mx-auto space-y-3" style={{ maxWidth: chatWidth }}>
            <ResizeHandle onMouseDown={onChatResize} />
            {conv.loadingOlder && (
              <div className="text-center text-[11px] text-muted py-1">
                Loading earlier messages…
              </div>
            )}
            {!conv.hasOlder && <SideChatSeedCard chat={chat} />}
            {visibleMessages.length === 0 && !streaming && (
              <div className="text-sm text-muted text-center pt-8">
                Send a message to start the conversation.
              </div>
            )}
            {visibleMessages.map((m) => (
              <TranscriptMessage
                key={m.id}
                message={m}
                streaming={streaming}
                retryable={m.id === conv.retryableId}
                onRetry={conv.retryTurn}
                onDelete={conv.deletion.requestDeleteMessage}
                compacted={
                  conv.compaction.markerId !== null && m.id > 0 && m.id < conv.compaction.markerId
                }
                doomed={rewindFrom !== null && m.id > 0 && m.id >= rewindFrom}
                cutAbove={m.id === rewindFrom}
                current={m.id === conv.timeline.currentMessageId}
                rewindActions={conv.rewindActions(m)}
                revealActions={
                  m.id === conv.timeline.currentMessageId &&
                  !conv.timeline.view.atLatest &&
                  !conv.rewind.preview
                }
                replyEdit={conv.replyEdit}
              />
            ))}
            <TranscriptTail
              visibleMessages={visibleMessages}
              streaming={streaming}
              pendingContent={conv.pendingContent}
              pendingReasoning={conv.pendingReasoning}
              onPickSuggestion={conv.sendSuggestion}
              onStop={conv.stop}
            />
          </div>
        </div>
          <TimelineRail timeline={conv.timeline} rewind={conv.rewind} streaming={streaming} />
        </div>

        <div className="border-t border-border p-3 pb-safe">
          <div className="mx-auto space-y-2" style={{ maxWidth: chatWidth }}>
            <RewindBar
              timeline={conv.timeline}
              rewind={conv.rewind}
              streaming={streaming}
              scrollRef={conv.scrollRef}
            />
            <UndoDeleteToasts deletion={conv.deletion} />
            <ConversationNotes
              notes={conv.notes}
              container="chat"
              containerId={chat.id}
              title={chat.title}
              hasIssue={false}
              allowPostComment={false}
            />
            {topicNote && (
              <CommandDraftCard
                title="Send to topic"
                subtitle={topicNote.topicTitle ?? chat.parent_topic_title ?? undefined}
                initialBody={topicNote.body}
                bodyPlaceholder="What this side chat concluded, in Markdown…"
                loading={topicNote.loading}
                posting={topicNote.posting}
                error={topicNote.error}
                sendLabel="Send to topic"
                postingLabel="Sending…"
                confirmHint="Filed into the topic as a note, with a link back to this chat."
                onSend={({ body }) => sendTopicNote(body)}
                onCancel={() => setTopicNote(null)}
              />
            )}
            <Composer
              value={composer.draft}
              onChange={composer.setDraft}
              onSend={() => void conv.send()}
              onStop={conv.stop}
              streaming={streaming}
              suggestions={composer.suggestions}
              userHistory={conv.userHistory}
              speech={composer.speech}
              interimText={composer.interimText}
              height={composerHeight}
              onResizeStart={onComposerResize}
              toolbarStart={
                <>
                  <ComposerModelControls />
                  <RoleSelector
                    value={chat.role_id ?? null}
                    onChange={(roleId) => void onSetRole?.(roleId)}
                    open={conv.roleOpen}
                    onOpenChange={conv.setRoleOpen}
                  />
                </>
              }
              attachments={conv.attachments}
            />
          </div>
        </div>
      </div>

      {showStats && (
        <ChatStatsPanel
          streamKey={streamKey}
          messages={conv.messages}
          kind="chat"
          containerId={chat.id}
          streaming={streaming}
          compaction={conv.compaction}
          reminder={{
            reminder: reminders.reminder,
            busy: reminders.reminderBusy,
            onEdit: () => reminders.setReminderModal({ note: "" }),
            onDone: () => void reminders.runReminderClear(true),
          }}
        />
      )}
      {reminders.reminderModal && (
        <ReminderModal
          container="chat"
          containerId={chat.id}
          existing={reminders.reminder}
          initialNote={reminders.reminderModal.note}
          onClose={() => reminders.setReminderModal(null)}
          onSaved={(saved) => {
            reminders.setReminderModal(null);
            reminders.handleReminderSaved(saved);
          }}
        />
      )}
      <NotesConfirmModal notes={conv.notes} />
    </div>
  );
}
