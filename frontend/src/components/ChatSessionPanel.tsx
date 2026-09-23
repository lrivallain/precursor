import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { ConversationNotes, NotesConfirmModal } from "./ConversationNotes";
import { TranscriptMessage, TranscriptTail } from "./ConversationTranscript";
import { ResizeHandle } from "./ResizeHandle";
import { api } from "../lib/api";
import { streamStore } from "../lib/streamStore";
import { useSettings } from "../lib/settingsStore";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useComposerInput } from "../lib/useComposerInput";
import { useConversation } from "../lib/useConversation";
import { useConfirm } from "./ConfirmDialog";
import { ReminderModal } from "./ReminderModal";
import { ReminderBanner } from "./ReminderBanner";
import type { Chat } from "../lib/types";
import { RoleSelector } from "./RoleSelector";

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
  const confirmAction = useConfirm();
  const settings = useSettings();
  const showStats = settings?.show_chat_stats ?? true;

  const composer = useComposerInput({ surface: "chat" });
  const conv = useConversation({
    kind: "chat",
    id: chat.id,
    composer,
    onCommand: dispatchCommand,
    onUpdated: onChatUpdated,
    onRemindersChanged,
    onSetRole,
  });
  const { streamKey, setPersisted, systemNote, visibleMessages, streaming, reminders } = conv;

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
    if (name === "clear") {
      if (
        !(await confirmAction({
          message: "Erase the entire transcript for this chat?",
          confirmLabel: "Erase transcript",
          variant: "danger",
        }))
      )
        return;
      try {
        await api.chats.clearMessages(chat.id);
        setPersisted([]);
        streamStore.clear(streamKey);
        onChatUpdated();
      } catch (err) {
        systemNote(`Clear failed: ${(err as Error).message}`);
      }
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
        <div ref={conv.scrollRef} onScroll={conv.onScroll} className="flex-1 overflow-y-auto p-4">
          <div className="relative mx-auto space-y-3" style={{ maxWidth: chatWidth }}>
            <ResizeHandle onMouseDown={onChatResize} />
            {conv.loadingOlder && (
              <div className="text-center text-[11px] text-muted py-1">
                Loading earlier messages…
              </div>
            )}
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
              />
            ))}
            <TranscriptTail
              visibleMessages={visibleMessages}
              streaming={streaming}
              pendingContent={conv.pendingContent}
              onPickSuggestion={conv.sendSuggestion}
              onStop={conv.stop}
            />
          </div>
        </div>

        <div className="border-t border-border p-3 pb-safe">
          <div className="mx-auto space-y-2" style={{ maxWidth: chatWidth }}>
            {conv.deletion.pendingDeletes.length > 0 && (
              <div className="flex flex-col gap-1">
                {conv.deletion.pendingDeletes.map((p) => (
                  <div
                    key={p.message.id}
                    className="flex items-center justify-between gap-2 rounded border border-border bg-surface px-3 py-1.5 text-xs"
                  >
                    <span className="truncate text-muted">Message deleted</span>
                    <button
                      className="shrink-0 rounded px-2 py-0.5 text-accent hover:bg-border"
                      onClick={() => conv.deletion.undoDelete(p.message.id)}
                    >
                      Undo
                    </button>
                  </div>
                ))}
              </div>
            )}
            <ConversationNotes
              notes={conv.notes}
              container="chat"
              containerId={chat.id}
              title={chat.title}
              hasIssue={false}
              allowPostComment={false}
            />
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

      {showStats && <ChatStatsPanel streamKey={streamKey} messages={conv.messages} />}
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
