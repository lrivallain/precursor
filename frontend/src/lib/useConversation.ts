import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { useConfirm } from "../components/ConfirmDialog";
import { api } from "./api";
import {
  commandsForSurface,
  formatMemoryList,
  nextSyntheticMessageId,
  parseMemoryStoreArg,
  parseMemoryUpdateArg,
} from "./commands";
import { rolesStore } from "./rolesStore";
import { skillsStore } from "./skillsStore";
import {
  containerFields,
  convKey,
  mergeConversation,
  streamStore,
  useStreamVersion,
  type ConvKind,
} from "./streamStore";
import { failedTurnUserMessageId } from "./systemNotice";
import { useChatScroll, type ChatScroll } from "./useChatScroll";
import type { ComposerInput } from "./useComposerInput";
import { useMessageDeletion, type MessageDeletion } from "./useMessageDeletion";
import {
  useNotesDraft,
  type NotesDraftController,
  type UseNotesDraftOptions,
} from "./useNotesDraft";
import { usePendingAttachments } from "./usePendingAttachments";
import { useReminders, type RemindersController } from "./useReminders";
import { useWindowedMessages } from "./useWindowedMessages";
import type { ComposerAttachments } from "../components/Composer";
import type { Message } from "./types";

/**
 * Erase a conversation's transcript and drop its client-side stream buffer, so a
 * finished turn still buffered (e.g. after a failed post-stream reload) can't
 * reappear. A reply still streaming is stopped first; otherwise its tail would
 * be persisted after the clear, orphaned from its prompt.
 */
export async function clearConversation(kind: ConvKind, id: number): Promise<void> {
  const key = convKey(kind, id);
  streamStore.stop(key);
  await api.container(kind, id).clearMessages();
  streamStore.clear(key);
}

export interface UseConversationOptions {
  kind: ConvKind;
  id: number;
  /** The composer feeding this conversation (see useComposerInput). */
  composer: ComposerInput;
  /**
   * Built-ins only this surface handles. Commands every persisted conversation
   * shares (notes, reminders, memory, role, clear) are dispatched here first.
   */
  onCommand: (name: string, argument: string) => Promise<void>;
  /** Refresh the sidebar and container after the transcript changes. */
  onUpdated: () => void;
  /** Refresh the sidebar Reminders section after a set / cancel / done. */
  onRemindersChanged?: () => void;
  /** Persist a role change for this conversation (null = default). */
  onSetRole?: (roleId: number | null) => Promise<void>;
  /** Topic-only: post the notes pad as a GitHub issue comment. */
  onPostComment?: UseNotesDraftOptions["onPostComment"];
}

export interface Conversation {
  streamKey: string;
  persisted: Message[];
  setPersisted: Dispatch<SetStateAction<Message[]>>;
  loadingOlder: boolean;
  /** Persisted window merged with the live buffered turn. */
  messages: Message[];
  /** `messages` minus rows pending an undoable delete. */
  visibleMessages: Message[];
  streaming: boolean;
  pendingContent: string;
  /** The prompt to offer a Retry on, when the transcript ends on an error. */
  retryableId: number | null;
  userHistory: string[];
  scrollRef: ChatScroll["scrollRef"];
  onScroll: ChatScroll["onScroll"];
  attachments: ComposerAttachments;
  deletion: Pick<MessageDeletion, "pendingDeletes" | "requestDeleteMessage" | "undoDelete">;
  notes: NotesDraftController;
  reminders: RemindersController;
  /** Whether the composer's role picker is open (a bare `/role` opens it). */
  roleOpen: boolean;
  setRoleOpen: Dispatch<SetStateAction<boolean>>;
  send: () => Promise<void>;
  sendSuggestion: (text: string) => void;
  retryTurn: (m: Message) => void;
  stop: () => void;
  /** Append a local-only system note to the transcript (not persisted). */
  systemNote: (content: string) => void;
}

/**
 * The streaming conversation behind a persisted container (a topic or a flat
 * chat): message window, live stream choreography, send / retry / stop, the
 * notes pad, reminders and the slash commands both surfaces share. Surfaces keep
 * their own markup and dispatch their own extra commands via `onCommand`.
 */
export function useConversation({
  kind,
  id,
  composer,
  onCommand,
  onUpdated,
  onRemindersChanged,
  onSetRole,
  onPostComment,
}: UseConversationOptions): Conversation {
  const confirmAction = useConfirm();
  const containerApi = useMemo(() => api.container(kind, id), [kind, id]);
  const handledCommands = useMemo(() => commandsForSurface(kind), [kind]);
  const fetchPage = useCallback(
    (opts: { limit: number; beforeId?: number }) => containerApi.listMessages(opts),
    [containerApi],
  );
  const win = useWindowedMessages({ fetchPage });
  const { persisted, setPersisted, loadingOlder } = win;
  const {
    pendingAttachments,
    setPendingAttachments,
    uploadingCount,
    attachmentError,
    uploadFiles,
    removeAttachment,
  } = usePendingAttachments({
    resetKey: id,
    upload: (file) => containerApi.uploadAttachment(file),
  });
  const { pendingDeletes, hiddenIds, requestDeleteMessage, undoDelete } = useMessageDeletion({
    resetKey: id,
    deleteMessage: (mid) => containerApi.deleteMessage(mid),
    setPersisted,
  });
  const [roleOpen, setRoleOpen] = useState(false);
  // Set while we handle a user-initiated Stop so the streaming→done effect
  // skips its own reload and lets stop() own the (post-persist) refresh.
  const stoppingRef = useRef(false);

  // Subscribe to the global streaming store. The store owns the AbortController
  // and SSE handler so a stream survives switching conversations.
  useStreamVersion();
  const streamKey = convKey(kind, id);
  const streaming = streamStore.isStreaming(streamKey);
  const pendingContent = streamStore.pendingContent(streamKey);
  const buffered = streamStore.bufferedMessages(streamKey);
  const hasSession = streamStore.hasSession(streamKey);
  const messages = useMemo<Message[]>(
    () => (hasSession ? mergeConversation(persisted, buffered) : persisted),
    [persisted, buffered, hasSession],
  );
  const visibleMessages = useMemo<Message[]>(
    () => messages.filter((m) => !hiddenIds.has(m.id)),
    [messages, hiddenIds],
  );
  // The prompt to offer a Retry on: set only while the transcript ends on an
  // error notice and nothing is streaming.
  const retryableId = useMemo<number | null>(
    () => (streaming ? null : failedTurnUserMessageId(visibleMessages)),
    [visibleMessages, streaming],
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

  const reminders = useReminders({
    container: kind,
    id,
    reload: reloadMessages,
    onRemindersChanged,
    systemNote,
  });

  const notes = useNotesDraft({
    container: kind,
    id,
    notesApi: containerApi.notes,
    appendMessages: (msgs) => {
      setPersisted((prev) => [...prev, ...msgs]);
      onUpdated();
    },
    startAppendAndAsk: (body, attachmentIds) =>
      void streamStore.start(streamKey, body, undefined, undefined, attachmentIds),
    onPostComment,
    systemNote,
  });

  const userHistory = useMemo(
    () => persisted.filter((m) => m.role === "user").map((m) => m.content),
    [persisted],
  );

  // Load persisted history when the conversation changes.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const msgs = await win.fetchFirstPage();
      if (cancelled) return;
      win.applyFirstPage(msgs);
      // If we just switched into a conversation whose session has already
      // finished, drop the buffered copy now that we have the canonical server
      // state.
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
  }, [streamKey]);

  // When a stream finishes, refetch persisted messages and discard the
  // buffered turn.
  const prevStreamingRef = useRef(streaming);
  useEffect(() => {
    prevStreamingRef.current = streamStore.isStreaming(streamKey);
  }, [streamKey]);
  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = streaming;
    if (!wasStreaming || streaming) return;
    // A user-initiated Stop persists + reloads in stop(); don't double-fetch.
    if (stoppingRef.current) {
      stoppingRef.current = false;
      return;
    }
    let cancelled = false;
    void (async () => {
      const msgs = await reloadMessages();
      if (cancelled || msgs === null) return;
      streamStore.clear(streamKey);
      onUpdated();
    })();
    return () => {
      cancelled = true;
    };
    // `onUpdated` is left out on purpose: callers pass an inline callback, and
    // re-running on its identity would cancel the in-flight reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, streamKey]);

  function localMessage(role: "user" | "system", content: string): Message {
    return {
      id: nextSyntheticMessageId(),
      ...containerFields(kind, id),
      role,
      content,
      tool_calls: null,
      created_at: new Date().toISOString(),
    };
  }

  function systemNote(content: string): void {
    setPersisted((prev) => [...prev, localMessage("system", content)]);
  }

  // Echo a locally-handled slash command into the transcript as a user turn so
  // it stays visible and is recallable via ↑ history (these commands never hit
  // the backend, so they aren't persisted server-side).
  function echoCommand(content: string): void {
    setPersisted((prev) => [...prev, localMessage("user", content)]);
  }

  async function send(): Promise<void> {
    const { draft, setDraft, speech, parseCommand } = composer;
    const content = draft.trim();
    const hasAttachments = pendingAttachments.length > 0;
    if ((!content && !hasAttachments) || streaming) return;
    pinToBottom();
    if (speech.listening) speech.stop();

    const cmd = content ? parseCommand(content) : null;
    if (cmd && handledCommands.has(cmd.name)) {
      setDraft("");
      echoCommand(content);
      if (!(await runSharedCommand(cmd.name, cmd.argument))) {
        await onCommand(cmd.name, cmd.argument);
      }
      return;
    }
    if (cmd) {
      const skill = skillsStore.byName(cmd.name);
      if (skill) {
        setDraft("");
        const expanded = `${skill.instructions.trim()}\n\n---\n\n${skillsStore.expandReferences(cmd.argument)}`;
        const atts = pendingAttachments;
        setPendingAttachments([]);
        // Persist the literal slash command as the user message;
        // the LLM receives the expanded prompt for this turn only.
        void streamStore.start(streamKey, content, expanded, atts);
        return;
      }
    }

    setDraft("");
    const atts = pendingAttachments;
    setPendingAttachments([]);
    // No leading skill command, but a `/skill-name` may appear mid-prompt: send
    // the expanded text to the LLM while the transcript keeps what was typed.
    const inlined = content ? skillsStore.expandReferences(content) : content;
    void streamStore.start(
      streamKey,
      content || "(attachment attached)",
      inlined && inlined !== content ? inlined : undefined,
      atts,
    );
  }

  function sendSuggestion(text: string): void {
    if (streaming || !text.trim()) return;
    pinToBottom();
    void streamStore.start(streamKey, text.trim());
  }

  /**
   * Replay a prompt whose turn ended in an error. The failed tail (partial
   * answer, tool rows, the error notice) is dropped locally right away and
   * deleted server-side by the retry, so the prompt is answered afresh instead
   * of piling a second copy onto the transcript.
   */
  function retryTurn(m: Message): void {
    if (streaming || m.id <= 0) return;
    pinToBottom();
    // Ids are monotonic, so "the failed tail" is everything above the prompt.
    // Client-side notes carry negative ids and are left alone.
    setPersisted((prev) => prev.filter((p) => p.id < m.id));
    streamStore.clear(streamKey);
    void streamStore.retry(streamKey, m.id, m.content, m.attachments);
  }

  function stop(): void {
    // Capture whatever the assistant has streamed so far, then cancel the
    // request. The backend persists only the *final* turn and each tool result
    // as it lands, neither of which runs once we disconnect — so we save the
    // partial reply and settle the tool calls still running as stopped
    // ourselves, instead of letting them vanish on the reload. stoppingRef
    // suppresses the streaming→done effect's reload so this handler owns the
    // post-persist refresh.
    const partial = streamStore.pendingContent(streamKey).trim();
    stoppingRef.current = true;
    const stoppedCalls = streamStore.stop(streamKey);
    void (async () => {
      try {
        if (partial || stoppedCalls.length > 0) {
          await containerApi.saveStopped({
            ...(partial ? { content: `${partial}\n\n_(stopped)_` } : {}),
            ...(stoppedCalls.length > 0 ? { tool_call_ids: stoppedCalls } : {}),
          });
        }
      } catch {
        // best-effort — keep going to refresh whatever did persist
      } finally {
        await reloadMessages();
        streamStore.clear(streamKey);
        onUpdated();
      }
    })();
  }

  /** Handle a command both persisted surfaces share; false when it isn't one. */
  async function runSharedCommand(name: string, argument: string): Promise<boolean> {
    switch (name) {
      case "notes":
        await notes.openNotesPad();
        return true;
      case "reminder":
        reminders.setReminderModal({ note: argument });
        return true;
      case "reminder-cancel":
        await reminders.runReminderClear(false);
        return true;
      case "done":
        await reminders.runReminderClear(true);
        return true;
      case "memory-store":
        await runMemoryStore(argument);
        return true;
      case "memory-list":
        await runMemoryList();
        return true;
      case "memory-update":
        await runMemoryUpdate(argument);
        return true;
      case "role":
        await runRole(argument);
        return true;
      case "clear":
        await runClear();
        return true;
      default:
        return false;
    }
  }

  async function runClear(): Promise<void> {
    if (
      !(await confirmAction({
        message: `Erase the entire transcript for this ${kind}?`,
        confirmLabel: "Erase transcript",
        variant: "danger",
      }))
    )
      return;
    try {
      await clearConversation(kind, id);
      setPersisted([]);
      onUpdated();
    } catch (err) {
      systemNote(`Clear failed: ${(err as Error).message}`);
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
    const { id: memoryId, ...patch } = parsed;
    try {
      const mem = await api.memories.update(memoryId, patch);
      systemNote(`Updated memory #${mem.id} [${mem.kind}].`);
    } catch (err) {
      systemNote(`Couldn't update memory #${memoryId}: ${(err as Error).message}`);
    }
  }

  async function runRole(argument: string): Promise<void> {
    const arg = argument.trim();
    if (!arg) {
      setRoleOpen(true);
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

  return {
    streamKey,
    persisted,
    setPersisted,
    loadingOlder,
    messages,
    visibleMessages,
    streaming,
    pendingContent,
    retryableId,
    userHistory,
    scrollRef,
    onScroll,
    attachments: {
      pending: pendingAttachments,
      uploadingCount,
      error: attachmentError,
      onFiles: uploadFiles,
      onRemove: removeAttachment,
    },
    deletion: { pendingDeletes, requestDeleteMessage, undoDelete },
    notes,
    reminders,
    roleOpen,
    setRoleOpen,
    send,
    sendSuggestion,
    retryTurn,
    stop,
    systemNote,
  };
}
