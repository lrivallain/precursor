import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageBubble } from "./MessageBubble";
import { SuggestedReplies } from "./SuggestedReplies";
import { ToolCallBubble } from "./ToolCallBubble";
import { NotesPanel } from "./NotesPanel";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { ResizeHandle } from "./ResizeHandle";
import { api } from "../lib/api";
import {
  splitSupportedAttachmentFiles,
  unsupportedAttachmentMessage,
} from "../lib/attachments";
import {
  commandsForSurface,
  formatMemoryList,
  matchSlashCommands,
  nextSyntheticMessageId,
  parseMemoryStoreArg,
  parseMemoryUpdateArg,
  parseSlashCommand,
  surfaceExcludes,
  type SlashCommand,
} from "../lib/commands";
import { skillsStore, useSkills } from "../lib/skillsStore";
import { rolesStore } from "../lib/rolesStore";
import { streamStore, useStreamVersion, convKey } from "../lib/streamStore";
import { detachedDraftStore } from "../lib/detachedDraftStore";
import { stripSuggestionBlock } from "../lib/suggestions";
import { useSettings } from "../lib/settingsStore";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useChatScroll } from "../lib/useChatScroll";
import { useWindowedMessages } from "../lib/useWindowedMessages";
import { useReminders } from "../lib/useReminders";
import { useNotesDraft } from "../lib/useNotesDraft";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { useConfirm } from "./ConfirmDialog";
import { ReminderModal } from "./ReminderModal";
import { ReminderBanner } from "./ReminderBanner";
import type {
  Attachment,
  Chat,
  Message,
} from "../lib/types";
import { TIMING, Z_INDEX } from "../lib/constants";
import { Modal } from "./Modal";

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
  /** Open the header role selector (used by bare `/role`). */
  onOpenRoleSelector?: () => void;
}

// Chats are flat sessions with no GitHub issue, so the gh-* commands, the
// tree-only /new and the topic-only /agent don't apply. The handled and
// excluded sets are derived from the catalog (see lib/commands.ts) so they
// can't drift from SLASH_COMMANDS.
const CHAT_EXCLUDED_COMMANDS: ReadonlySet<string> = surfaceExcludes("chat");
const HANDLED_COMMANDS = commandsForSurface("chat");

interface ParsedToolMeta {
  tool_call_id?: string;
  name?: string;
  arguments?: string;
  is_error?: boolean;
  pending?: boolean;
}

function parseToolMeta(raw: string | null): ParsedToolMeta | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as ParsedToolMeta;
    return typeof v === "object" && v !== null ? v : null;
  } catch {
    return null;
  }
}

export function ChatSessionPanel({
  chat,
  onChatUpdated,
  onArchived,
  onRemindersChanged,
  onSetRole,
  onOpenRoleSelector,
}: ChatSessionPanelProps) {
  const confirmAction = useConfirm();
  const fetchPage = useCallback(
    (opts: { limit: number; beforeId?: number }) => api.chats.listMessages(chat.id, opts),
    [chat.id],
  );
  const win = useWindowedMessages({ fetchPage });
  const { persisted, setPersisted, loadingOlder } = win;
  const [draft, setDraft] = useState("");
  const [pendingDeletes, setPendingDeletes] = useState<
    { message: Message; timer: number }[]
  >([]);
  // Images uploaded but not yet bound to a sent message. They live as orphan
  // rows server-side until /messages/stream binds them, or the user removes
  // them / leaves the chat (in which case we DELETE them).
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const pendingAttachmentsRef = useRef<Attachment[]>([]);
  const stoppingRef = useRef(false);

  useStreamVersion();
  const settings = useSettings();
  const showStats = settings?.show_chat_stats ?? true;

  const streamKey = convKey("chat", chat.id);
  const streaming = streamStore.isStreaming(streamKey);
  const pendingContent = streamStore.pendingContent(streamKey);
  const buffered = streamStore.bufferedMessages(streamKey);
  const hasSession = streamStore.hasSession(streamKey);
  const messages = useMemo<Message[]>(
    () => (hasSession ? [...persisted, ...buffered] : persisted),
    [persisted, buffered, hasSession],
  );
  const hiddenIds = useMemo(
    () => new Set(pendingDeletes.map((p) => p.message.id)),
    [pendingDeletes],
  );
  const visibleMessages = useMemo<Message[]>(
    () => messages.filter((m) => !hiddenIds.has(m.id)),
    [messages, hiddenIds],
  );

  // Reverse-infinite-scroll wiring lives in useWindowedMessages; bind the scroll
  // helpers back into the hook once useChatScroll has produced them.
  const { scrollRef, onScroll, captureTopAnchor, pinToBottom } = useChatScroll(
    [messages, pendingContent],
    win.onReachTop,
  );
  const { bindScroll, reloadMessages } = win;
  useEffect(() => {
    bindScroll({ captureTopAnchor, pinToBottom });
  }, [bindScroll, captureTopAnchor, pinToBottom]);

  const {
    reminder,
    reminderModal,
    setReminderModal,
    reminderBusy,
    handleReminderSaved,
    runReminderClear,
  } = useReminders({
    container: "chat",
    id: chat.id,
    reload: reloadMessages,
    onRemindersChanged,
    systemNote,
  });

  const notesApi = useMemo(
    () => ({
      getDraft: () => api.chats.getNotesDraft(chat.id),
      saveDraft: (text: string) => api.chats.saveNotesDraft(chat.id, text),
      clearDraft: () => api.chats.clearNotesDraft(chat.id),
      append: (text: string, ids: number[]) => api.chats.appendNotes(chat.id, text, ids),
      rephrase: (text: string) => api.chats.rephraseNotes(chat.id, text),
      uploadAttachment: (file: File) => api.chats.uploadNoteAttachment(chat.id, file),
      deleteAttachment: (attId: number) => api.chats.deleteNoteAttachment(chat.id, attId),
    }),
    [chat.id],
  );
  const {
    pendingNotes,
    savedNotesDraft,
    notesConfirm,
    resolveNotesConfirm,
    openNotesPad,
    resumeSavedNotesDraft,
    discardSavedNotesDraft,
    uploadNoteAttachments,
    removeNoteAttachment,
    rephraseNotes,
    saveNotesDraft,
    runNotesAction,
    closeNotesPad,
    dismissPad,
  } = useNotesDraft({
    container: "chat",
    id: chat.id,
    notesApi,
    appendMessages: (msgs) => {
      setPersisted((prev) => [...prev, ...msgs]);
      onChatUpdated();
    },
    startAppendAndAsk: (body, attachmentIds) =>
      void streamStore.start(streamKey, body, undefined, undefined, attachmentIds),
    systemNote,
  });

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

  // Live speech-to-text (when Azure is configured server-side).
  const [interimText, setInterimText] = useState("");
  const appendFinalChunk = (text: string): void => {
    const chunk = text.trim();
    if (!chunk) return;
    setDraft((d) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk));
    setInterimText("");
  };
  const azureReady = settings?.stt_azure_ready ?? false;
  const sttLanguage = settings?.azure_speech_language || undefined;
  const speech = useAzureSpeech({
    onFinalChunk: appendFinalChunk,
    onInterim: setInterimText,
    enabled: azureReady,
    lang: sttLanguage,
  });
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

  const skills = useSkills();
  const skillCommands = useMemo<SlashCommand[]>(
    () =>
      skills
        .filter((s) => s.active)
        .map((s) => ({
          name: s.name,
          label: `/${s.name}`,
          description: s.description ?? "",
          kind: "skill" as const,
          argumentHint: "input",
        })),
    [skills],
  );
  const suggestions = useMemo<SlashCommand[]>(
    () => matchSlashCommands(draft, skillCommands, CHAT_EXCLUDED_COMMANDS) ?? [],
    [draft, skillCommands],
  );

  const userHistory = useMemo(
    () => persisted.filter((m) => m.role === "user").map((m) => m.content),
    [persisted],
  );

  // Load persisted history when the chat changes.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const msgs = await win.fetchFirstPage();
      if (cancelled) return;
      win.applyFirstPage(msgs);
      if (
        streamStore.hasSession(streamKey) &&
        !streamStore.isStreaming(streamKey)
      ) {
        streamStore.clear(streamKey);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.id]);

  const prevStreamingRef = useRef(streaming);
  useEffect(() => {
    prevStreamingRef.current = streamStore.isStreaming(streamKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.id]);
  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = streaming;
    if (!wasStreaming || streaming) return;
    if (stoppingRef.current) {
      stoppingRef.current = false;
      return;
    }
    let cancelled = false;
    void (async () => {
      const msgs = await reloadMessages();
      if (cancelled || msgs === null) return;
      streamStore.clear(streamKey);
      onChatUpdated();
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, chat.id]);

  // Flush queued message deletions on unmount / chat switch.
  const pendingDeletesRef = useRef<typeof pendingDeletes>([]);
  useEffect(() => {
    pendingDeletesRef.current = pendingDeletes;
  }, [pendingDeletes]);
  useEffect(() => {
    const cid = chat.id;
    return () => {
      const queued = pendingDeletesRef.current;
      pendingDeletesRef.current = [];
      for (const p of queued) {
        window.clearTimeout(p.timer);
        void api.chats.deleteMessage(cid, p.message.id).catch(() => {});
      }
    };
  }, [chat.id]);

  // Keep a ref so the unmount/switch cleanup reads the latest list.
  useEffect(() => {
    pendingAttachmentsRef.current = pendingAttachments;
  }, [pendingAttachments]);

  // When the chat changes, drop unsent attachments + delete them server-side
  // so they don't accumulate as orphan rows.
  useEffect(() => {
    return () => {
      const orphans = pendingAttachmentsRef.current;
      pendingAttachmentsRef.current = [];
      setPendingAttachments([]);
      setAttachmentError(null);
      for (const a of orphans) {
        void api.attachments.remove(a.id).catch(() => {});
      }
    };
  }, [chat.id]);

  async function uploadFiles(files: Iterable<File>): Promise<void> {
    const { supported, unsupported } = splitSupportedAttachmentFiles(files);
    if (unsupported.length > 0) {
      setAttachmentError(unsupportedAttachmentMessage(unsupported));
    }
    if (supported.length === 0) return;
    if (unsupported.length === 0) setAttachmentError(null);
    setUploadingCount((n) => n + supported.length);
    try {
      for (const file of supported) {
        try {
          const att = await api.attachments.uploadForChat(chat.id, file);
          setPendingAttachments((prev) => [...prev, att]);
        } catch (err) {
          setAttachmentError((err as Error).message || "Upload failed");
        }
      }
    } finally {
      setUploadingCount((n) => Math.max(0, n - supported.length));
    }
  }

  async function removeAttachment(id: number): Promise<void> {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
    try {
      await api.attachments.remove(id);
    } catch {
      // Already gone server-side, or bound to a sent message — nothing to do.
    }
  }

  function systemNote(content: string): void {
    setPersisted((prev) => [
      ...prev,
      {
        id: nextSyntheticMessageId(),
        topic_id: null,
        chat_id: chat.id,
        role: "system",
        content,
        tool_calls: null,
        created_at: new Date().toISOString(),
      },
    ]);
  }

  // Echo a locally-handled slash command into the transcript as a user turn so
  // it stays visible and is recallable via ↑ history (these commands never hit
  // the backend, so they aren't persisted server-side).
  function echoCommand(content: string): void {
    setPersisted((prev) => [
      ...prev,
      {
        id: nextSyntheticMessageId(),
        topic_id: null,
        chat_id: chat.id,
        role: "user",
        content,
        tool_calls: null,
        created_at: new Date().toISOString(),
      },
    ]);
  }

  function commitDelete(messageId: number): void {
    setPendingDeletes((prev) => prev.filter((p) => p.message.id !== messageId));
    setPersisted((prev) => prev.filter((m) => m.id !== messageId));
    void api.chats.deleteMessage(chat.id, messageId).catch(() => {});
  }
  function requestDeleteMessage(message: Message): void {
    if (pendingDeletesRef.current.some((p) => p.message.id === message.id)) return;
    const timer = window.setTimeout(() => commitDelete(message.id), TIMING.UNDO_DELETE_MS);
    setPendingDeletes((prev) => [...prev, { message, timer }]);
  }
  function undoDelete(messageId: number): void {
    setPendingDeletes((prev) => {
      const hit = prev.find((p) => p.message.id === messageId);
      if (hit) window.clearTimeout(hit.timer);
      return prev.filter((p) => p.message.id !== messageId);
    });
  }

  async function dispatchCommand(name: string, argument: string): Promise<void> {
    if (name === "notes") {
      await openNotesPad();
      return;
    }
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
      return;
    }
    if (name === "reminder") {
      setReminderModal({ note: argument });
      return;
    }
    if (name === "reminder-cancel") {
      await runReminderClear(false);
      return;
    }
    if (name === "done") {
      await runReminderClear(true);
      return;
    }
    if (name === "memory-store") {
      await runMemoryStore(argument);
      return;
    }
    if (name === "memory-list") {
      await runMemoryList();
      return;
    }
    if (name === "memory-update") {
      await runMemoryUpdate(argument);
      return;
    }
    if (name === "role") {
      const arg = argument.trim();
      if (!arg) {
        onOpenRoleSelector?.();
        return;
      }
      await rolesStore.ensureLoaded();
      const role = rolesStore.byName(arg);
      if (!role) {
        systemNote(`Unknown role "${arg}". Manage roles in Settings → Roles.`);
        return;
      }
      try {
        await onSetRole?.(role.is_default ? null : role.id);
        systemNote(`Assistant role set to "${role.name}".`);
      } catch (err) {
        systemNote(`Role change failed: ${(err as Error).message}`);
      }
    }
  }

  async function runMemoryStore(argument: string): Promise<void> {
    const parsed = parseMemoryStoreArg(argument);
    if (!parsed) return systemNote("Usage: `/memory-store [kind] <content>`");
    try {
      const mem = await api.memories.create(parsed);
      systemNote(`Saved memory #${mem.id} [${mem.kind}]. Manage in Settings → Memory.`);
    } catch (err) {
      systemNote(`Couldn't save memory: ${(err as Error).message}`);
    }
  }

  async function runMemoryList(): Promise<void> {
    try {
      const memories = await api.memories.list();
      systemNote(formatMemoryList(memories));
    } catch (err) {
      systemNote(`Couldn't list memories: ${(err as Error).message}`);
    }
  }

  async function runMemoryUpdate(argument: string): Promise<void> {
    const parsed = parseMemoryUpdateArg(argument);
    if (!parsed) return systemNote("Usage: `/memory-update <id> [kind] <content>`");
    const { id, ...patch } = parsed;
    try {
      const mem = await api.memories.update(id, patch);
      systemNote(`Updated memory #${mem.id} [${mem.kind}].`);
    } catch (err) {
      systemNote(`Couldn't update memory #${id}: ${(err as Error).message}`);
    }
  }

  async function send(): Promise<void> {
    const content = draft.trim();
    const hasAttachments = pendingAttachments.length > 0;
    if ((!content && !hasAttachments) || streaming) return;
    pinToBottom();
    if (speech.listening) speech.stop();

    const cmd = content
      ? parseSlashCommand(content, skillCommands, CHAT_EXCLUDED_COMMANDS)
      : null;
    if (cmd && HANDLED_COMMANDS.has(cmd.name)) {
      setDraft("");
      echoCommand(content);
      await dispatchCommand(cmd.name, cmd.argument);
      return;
    }
    if (cmd) {
      const skill = skillsStore.byName(cmd.name);
      if (skill) {
        setDraft("");
        const expanded = `${skill.instructions.trim()}\n\n---\n\n${cmd.argument}`;
        const atts = pendingAttachments;
        setPendingAttachments([]);
        void streamStore.start(streamKey, content, expanded, atts);
        return;
      }
    }
    setDraft("");
    const atts = pendingAttachments;
    setPendingAttachments([]);
    void streamStore.start(
      streamKey,
      content || "(attachment attached)",
      undefined,
      atts,
    );
  }

  function sendSuggestion(text: string): void {
    if (streaming || !text.trim()) return;
    pinToBottom();
    void streamStore.start(streamKey, text.trim());
  }

  function stop(): void {
    const partial = streamStore.pendingContent(streamKey).trim();
    stoppingRef.current = true;
    streamStore.stop(streamKey);
    void (async () => {
      try {
        if (partial) {
          await api.chats.saveStoppedMessage(chat.id, `${partial}\n\n_(stopped)_`);
        }
      } catch {
        // best-effort
      } finally {
        await reloadMessages();
        streamStore.clear(streamKey);
        onChatUpdated();
      }
    })();
  }

  return (
    <div className="h-full flex min-h-0">
      <div className="flex-1 flex flex-col min-h-0">
        {reminder && reminder.status === "fired" && (
          <ReminderBanner
            reminder={reminder}
            busy={reminderBusy}
            onDone={() => void runReminderClear(true)}
          />
        )}
        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto p-4">
          <div className="relative mx-auto space-y-3" style={{ maxWidth: chatWidth }}>
            <ResizeHandle onMouseDown={onChatResize} />
            {loadingOlder && (
              <div className="text-center text-[11px] text-muted py-1">
                Loading earlier messages…
              </div>
            )}
            {visibleMessages.length === 0 && !streaming && (
              <div className="text-sm text-muted text-center pt-8">
                Send a message to start the conversation.
              </div>
            )}
            {visibleMessages.map((m) => {
              if (m.role === "tool") {
                const meta = parseToolMeta(m.tool_calls);
                return (
                  <ToolCallBubble
                    key={m.id}
                    name={meta?.name ?? "(unknown)"}
                    arguments={meta?.arguments ?? "{}"}
                    content={meta?.pending ? null : m.content}
                    isError={Boolean(meta?.is_error)}
                    pending={Boolean(meta?.pending)}
                  />
                );
              }
              if (m.role === "assistant" && !m.content.trim() && m.tool_calls) {
                return null;
              }
              const canDelete =
                !streaming && m.id > 0 && (m.role === "user" || m.role === "assistant");
              return (
                <MessageBubble
                  key={m.id}
                  role={m.role}
                  content={m.content}
                  attachments={m.attachments}
                  agentSessionId={m.agent_session_id}
                  createdAt={m.created_at}
                  model={m.model}
                  elapsedMs={m.elapsed_ms}
                  onDelete={canDelete ? () => requestDeleteMessage(m) : undefined}
                />
              );
            })}
            {!streaming &&
              (() => {
                const last = visibleMessages[visibleMessages.length - 1];
                if (last?.role === "assistant" && last.suggestions?.length) {
                  return (
                    <SuggestedReplies
                      items={last.suggestions}
                      onPick={sendSuggestion}
                      disabled={streaming}
                    />
                  );
                }
                return null;
              })()}
            {streaming && (
              <MessageBubble
                role="assistant"
                content={stripSuggestionBlock(pendingContent)}
                pending
                onStop={stop}
              />
            )}
          </div>
        </div>

        <div className="border-t border-border p-3">
          <div className="mx-auto space-y-2" style={{ maxWidth: chatWidth }}>
            {pendingDeletes.length > 0 && (
              <div className="flex flex-col gap-1">
                {pendingDeletes.map((p) => (
                  <div
                    key={p.message.id}
                    className="flex items-center justify-between gap-2 rounded border border-border bg-surface px-3 py-1.5 text-xs"
                  >
                    <span className="truncate text-muted">Message deleted</span>
                    <button
                      className="shrink-0 rounded px-2 py-0.5 text-accent hover:bg-border"
                      onClick={() => undoDelete(p.message.id)}
                    >
                      Undo
                    </button>
                  </div>
                ))}
              </div>
            )}
            {!pendingNotes && savedNotesDraft && (
              <div className="flex items-center justify-between gap-2 rounded border border-border bg-surface px-3 py-1.5 text-xs">
                <span className="min-w-0 flex-1 truncate text-muted">
                  Saved notes draft:
                  {savedNotesDraft.text ? ` ${savedNotesDraft.text}` : ""}
                  {savedNotesDraft.attachmentCount > 0
                    ? ` (${savedNotesDraft.attachmentCount} attachment${
                        savedNotesDraft.attachmentCount > 1 ? "s" : ""
                      })`
                    : ""}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    className="shrink-0 rounded px-2 py-0.5 text-accent hover:bg-border"
                    onClick={() => void resumeSavedNotesDraft()}
                  >
                    Resume
                  </button>
                  <button
                    className="shrink-0 rounded px-2 py-0.5 text-muted hover:bg-border"
                    onClick={() => void discardSavedNotesDraft()}
                  >
                    Discard
                  </button>
                </div>
              </div>
            )}
            {pendingNotes && (
              <NotesPanel
                hasIssue={false}
                allowPostComment={false}
                initialText={pendingNotes.initialText}
                loadingDraft={pendingNotes.loadingDraft}
                savingDraft={pendingNotes.savingDraft}
                rephrasing={pendingNotes.rephrasing}
                acting={pendingNotes.acting}
                error={pendingNotes.error}
                attachments={pendingNotes.attachments}
                uploadingAttachments={pendingNotes.uploadingAttachments}
                attachmentsError={pendingNotes.attachmentsError}
                rephrasedText={pendingNotes.rephrasedText}
                onRephrase={rephraseNotes}
                onSaveDraft={saveNotesDraft}
                onAction={runNotesAction}
                onAttachFiles={uploadNoteAttachments}
                onRemoveAttachment={removeNoteAttachment}
                onCancel={closeNotesPad}
                onPopOut={
                  pendingNotes.loadingDraft
                    ? undefined
                    : (text) => {
                        detachedDraftStore.open({
                          kind: "notes",
                          container: "chat",
                          containerId: chat.id,
                          title: `Notes — ${chat.title}`,
                          hasIssue: false,
                          allowPostComment: false,
                          initialText: text,
                          initialAttachments: pendingNotes.attachments,
                        });
                        dismissPad();
                      }
                }
              />
            )}
            <Composer
              value={draft}
              onChange={setDraft}
              onSend={() => void send()}
              onStop={stop}
              streaming={streaming}
              suggestions={suggestions}
              userHistory={userHistory}
              speech={speech}
              interimText={interimText}
              height={composerHeight}
              onResizeStart={onComposerResize}
              toolbarStart={<ComposerModelControls />}
              attachments={{
                pending: pendingAttachments,
                uploadingCount,
                error: attachmentError,
                onFiles: uploadFiles,
                onRemove: removeAttachment,
              }}
            />
          </div>
        </div>
      </div>

      {showStats && <ChatStatsPanel streamKey={streamKey} messages={messages} />}
      {reminderModal && (
        <ReminderModal
          container="chat"
          containerId={chat.id}
          existing={reminder}
          initialNote={reminderModal.note}
          onClose={() => setReminderModal(null)}
          onSaved={(saved) => {
            setReminderModal(null);
            handleReminderSaved(saved);
          }}
        />
      )}
      {notesConfirm && (
        <Modal
          zIndex={Z_INDEX.MODAL_NESTED}
          padded
          closeOnBackdrop={false}
          panelClassName="w-full max-w-sm rounded-lg border border-border bg-surface p-4 shadow-2xl"
        >
          <div className="text-sm">{notesConfirm.message}</div>
          <div className="mt-4 flex justify-end gap-2">
            <button
              className="rounded border border-border px-3 py-1.5 text-xs hover:bg-bg"
              onClick={() => resolveNotesConfirm(false)}
            >
              Cancel
            </button>
            <button
              className="rounded bg-accent px-3 py-1.5 text-xs text-white"
              onClick={() => resolveNotesConfirm(true)}
            >
              Confirm
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
