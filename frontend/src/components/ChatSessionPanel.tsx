import { useEffect, useMemo, useRef, useState } from "react";
import { MessageBubble } from "./MessageBubble";
import { ToolCallBubble } from "./ToolCallBubble";
import { NotesPanel, type NotesAction } from "./NotesPanel";
import { Composer } from "./Composer";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { ResizeHandle } from "./ResizeHandle";
import { api } from "../lib/api";
import {
  splitSupportedAttachmentFiles,
  unsupportedAttachmentMessage,
} from "../lib/attachments";
import {
  GITHUB_SLASH_COMMANDS,
  matchSlashCommands,
  parseSlashCommand,
  type SlashCommand,
} from "../lib/commands";
import { skillsStore, useSkills } from "../lib/skillsStore";
import { rolesStore } from "../lib/rolesStore";
import { streamStore, useStreamVersion, convKey } from "../lib/streamStore";
import { useSettings } from "../lib/settingsStore";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { useConfirm } from "./ConfirmDialog";
import { ReminderModal } from "./ReminderModal";
import { ReminderBanner } from "./ReminderBanner";
import type {
  Attachment,
  Chat,
  Message,
  NoteDraftAttachment,
  Reminder,
} from "../lib/types";

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

// Chats are flat sessions with no GitHub issue, so the gh-* commands and the
// tree-only /new don't apply. Everything else (skills, notes, rename, pin,
// clear, archive) behaves exactly like a topic.
const CHAT_EXCLUDED_COMMANDS: ReadonlySet<string> = new Set([
  ...GITHUB_SLASH_COMMANDS,
  "new",
]);
const HANDLED_COMMANDS = new Set<string>([
  "notes",
  "rename",
  "pin",
  "unpin",
  "clear",
  "archive",
  "reminder",
  "reminder-cancel",
  "done",
  "role",
]);

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

interface PendingNotes {
  initialText: string;
  attachments: NoteDraftAttachment[];
  uploadingAttachments: number;
  attachmentsError: string | null;
  loadingDraft: boolean;
  savingDraft: boolean;
  rephrasing: boolean;
  acting: boolean;
  error: string | null;
  rephrasedText?: string;
}

interface NotesConfirmState {
  message: string;
  resolve: (ok: boolean) => void;
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
  const [persisted, setPersisted] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [pendingNotes, setPendingNotes] = useState<PendingNotes | null>(null);
  const [savedNotesDraft, setSavedNotesDraft] = useState<{
    text: string;
    attachmentCount: number;
  } | null>(null);
  const [notesConfirm, setNotesConfirm] = useState<NotesConfirmState | null>(null);
  // One-shot reminder state for this chat (null = none set).
  const [reminder, setReminder] = useState<Reminder | null>(null);
  const [reminderModal, setReminderModal] = useState<{ note: string } | null>(null);
  const [reminderBusy, setReminderBusy] = useState(false);
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
  const scrollRef = useRef<HTMLDivElement>(null);
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
      skills.map((s) => ({
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
      const msgs = await api.listChatMessages(chat.id).catch(() => []);
      if (cancelled) return;
      setPersisted(msgs);
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

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const res = await api.getChatNotesDraft(chat.id).catch(() => null);
      if (cancelled) return;
      const text = (res?.text ?? "").trim();
      setSavedNotesDraft(
        text || (res?.attachments.length ?? 0)
          ? { text, attachmentCount: res?.attachments.length ?? 0 }
          : null,
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [chat.id]);

  // Load this chat's reminder (if any) so we can show the fired banner and
  // prefill the edit modal. Re-runs when the panel remounts after a fire.
  const refreshReminder = useMemo(
    () => async () => {
      try {
        setReminder(await api.getReminder("chat", chat.id));
      } catch {
        setReminder(null); // 404 => no reminder
      }
    },
    [chat.id],
  );
  useEffect(() => {
    void refreshReminder();
  }, [refreshReminder]);
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
      const msgs = await api.listChatMessages(chat.id).catch(() => null);
      if (cancelled || msgs === null) return;
      setPersisted(msgs);
      streamStore.clear(streamKey);
      onChatUpdated();
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, chat.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pendingContent]);

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
        void api.deleteChatMessage(cid, p.message.id).catch(() => {});
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
        void api.deleteAttachment(a.id).catch(() => {});
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
          const att = await api.uploadChatAttachment(chat.id, file);
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
      await api.deleteAttachment(id);
    } catch {
      // Already gone server-side, or bound to a sent message — nothing to do.
    }
  }

  function systemNote(content: string): void {
    setPersisted((prev) => [
      ...prev,
      {
        id: -Date.now(),
        topic_id: null,
        chat_id: chat.id,
        role: "system",
        content,
        tool_calls: null,
        created_at: new Date().toISOString(),
      },
    ]);
  }

  function commitDelete(messageId: number): void {
    setPendingDeletes((prev) => prev.filter((p) => p.message.id !== messageId));
    setPersisted((prev) => prev.filter((m) => m.id !== messageId));
    void api.deleteChatMessage(chat.id, messageId).catch(() => {});
  }
  function requestDeleteMessage(message: Message): void {
    if (pendingDeletesRef.current.some((p) => p.message.id === message.id)) return;
    const timer = window.setTimeout(() => commitDelete(message.id), 5000);
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
      setPendingNotes({
        initialText: "",
        attachments: [],
        uploadingAttachments: 0,
        attachmentsError: null,
        loadingDraft: true,
        savingDraft: false,
        rephrasing: false,
        acting: false,
        error: null,
      });
      try {
        const draftRes = await api.getChatNotesDraft(chat.id);
        const loaded = (draftRes.text ?? "").trim();
        setSavedNotesDraft(
          loaded || draftRes.attachments.length
            ? { text: loaded, attachmentCount: draftRes.attachments.length }
            : null,
        );
        setPendingNotes((p) =>
          p
            ? {
                ...p,
                initialText: draftRes.text ?? "",
                attachments: draftRes.attachments,
                loadingDraft: false,
              }
            : p,
        );
      } catch (err) {
        setPendingNotes((p) =>
          p
            ? {
                ...p,
                loadingDraft: false,
                error: (err as Error).message,
              }
            : p,
        );
      }
      return;
    }
    if (name === "rename") {
      const title = argument.trim();
      if (!title) return systemNote("Usage: `/rename <new title>`");
      try {
        await api.updateChat(chat.id, { title });
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
        await api.updateChat(chat.id, { pinned });
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
        await api.clearChatMessages(chat.id);
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
        await api.archiveChat(chat.id);
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

  // Re-fetch the persisted transcript (the backend records reminder set/clear
  // confirmations as system messages, and our own SSE echo is suppressed).
  async function reloadPersisted(): Promise<void> {
    try {
      setPersisted(await api.listChatMessages(chat.id));
    } catch {
      // keep what we have
    }
  }

  // Apply a modal save: update local state and pull in the confirmation the
  // backend just appended to the transcript.
  function handleReminderSaved(saved: Reminder | null): void {
    setReminder(saved);
    void reloadPersisted();
    onRemindersChanged?.();
  }

  // Shared by /reminder-cancel (any reminder) and /done (a fired one). The
  // backend DELETE is the same operation; the messages differ.
  async function runReminderClear(requireFired: boolean): Promise<void> {
    if (!reminder) {
      systemNote(requireFired ? "No active reminder to mark done." : "No reminder set.");
      return;
    }
    if (requireFired && reminder.status !== "fired") {
      systemNote("This reminder hasn't fired yet. Use `/reminder-cancel` to remove it.");
      return;
    }
    setReminderBusy(true);
    try {
      await api.clearReminder("chat", chat.id);
      setReminder(null);
      await reloadPersisted();
      onRemindersChanged?.();
    } catch (err) {
      systemNote(`Reminder update failed: ${(err as Error).message}`);
    } finally {
      setReminderBusy(false);
    }
  }

  async function uploadNoteAttachments(files: Iterable<File>): Promise<void> {
    if (!pendingNotes) return;
    const { supported, unsupported } = splitSupportedAttachmentFiles(files);
    if (supported.length === 0) {
      if (unsupported.length > 0) {
        setPendingNotes((p) =>
          p ? { ...p, attachmentsError: unsupportedAttachmentMessage(unsupported) } : p,
        );
      }
      return;
    }
    setPendingNotes((p) =>
      p
        ? {
            ...p,
            attachmentsError:
              unsupported.length > 0 ? unsupportedAttachmentMessage(unsupported) : null,
            uploadingAttachments: p.uploadingAttachments + supported.length,
          }
        : p,
    );
    try {
      for (const file of supported) {
        try {
          const att = await api.uploadChatNoteAttachment(chat.id, file);
          setPendingNotes((p) => (p ? { ...p, attachments: [...p.attachments, att] } : p));
        } catch (err) {
          setPendingNotes((p) =>
            p ? { ...p, attachmentsError: (err as Error).message || "Upload failed" } : p,
          );
        }
      }
    } finally {
      setPendingNotes((p) =>
        p
          ? {
              ...p,
              uploadingAttachments: Math.max(0, p.uploadingAttachments - supported.length),
            }
          : p,
      );
    }
  }

  async function removeNoteAttachment(id: number): Promise<void> {
    if (!pendingNotes) return;
    setPendingNotes((p) =>
      p ? { ...p, attachments: p.attachments.filter((a) => a.id !== id) } : p,
    );
    try {
      await api.deleteChatNoteAttachment(chat.id, id);
    } catch {
      // ignore stale/deleted ids
    }
  }

  async function rephraseNotes(text: string): Promise<void> {
    if (!pendingNotes || !text.trim()) return;
    setPendingNotes((p) => (p ? { ...p, rephrasing: true, error: null } : p));
    try {
      const res = await api.rephraseChatNotes(chat.id, text);
      setPendingNotes((p) => (p ? { ...p, rephrasing: false, rephrasedText: res.text } : p));
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, rephrasing: false, error: (err as Error).message } : p,
      );
    }
  }

  async function runNotesAction(action: NotesAction, text: string): Promise<void> {
    if (!pendingNotes) return;
    const trimmed = text.trim();
    const attachmentIds = pendingNotes.attachments.map((a) => a.id);
    if (!trimmed && attachmentIds.length === 0) return;
    setPendingNotes((p) => (p ? { ...p, acting: true, error: null } : p));
    try {
      if (action === "append") {
        const res = await api.appendChatNotes(chat.id, trimmed, attachmentIds);
        await api.clearChatNotesDraft(chat.id).catch(() => {});
        setSavedNotesDraft(null);
        setPersisted((prev) => [...prev, res.message]);
        onChatUpdated();
        setPendingNotes(null);
      } else if (action === "append-and-ask") {
        setSavedNotesDraft(null);
        setPendingNotes(null);
        const body = trimmed ? `**Notes**\n\n${trimmed}` : "**Notes**";
        void streamStore.start(streamKey, body, undefined, undefined, attachmentIds);
      }
      // "post-comment" is GitHub-only and never offered for chats.
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, acting: false, error: (err as Error).message } : p,
      );
    }
  }

  async function saveNotesDraft(text: string): Promise<void> {
    if (!pendingNotes) return;
    if (!text.trim() && pendingNotes.attachments.length === 0) return;
    if (!(await askNotesConfirm("Save notes as draft and close the pad?"))) return;
    setPendingNotes((p) => (p ? { ...p, savingDraft: true, error: null } : p));
    try {
      const res = await api.saveChatNotesDraft(chat.id, text.trim());
      const saved = (res.text ?? "").trim();
      setSavedNotesDraft(
        saved || res.attachments.length
          ? { text: saved, attachmentCount: res.attachments.length }
          : null,
      );
      setPendingNotes(null);
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, savingDraft: false, error: (err as Error).message } : p,
      );
    }
  }

  async function discardSavedNotesDraft(): Promise<void> {
    if (!(await askNotesConfirm("Discard the saved notes draft?"))) return;
    try {
      await api.clearChatNotesDraft(chat.id);
      setSavedNotesDraft(null);
    } catch (err) {
      systemNote(`Draft discard failed: ${(err as Error).message}`);
    }
  }

  async function resumeSavedNotesDraft(): Promise<void> {
    setPendingNotes({
      initialText: "",
      attachments: [],
      uploadingAttachments: 0,
      attachmentsError: null,
      loadingDraft: true,
      savingDraft: false,
      rephrasing: false,
      acting: false,
      error: null,
    });
    try {
      const draftRes = await api.getChatNotesDraft(chat.id);
      setPendingNotes((p) =>
        p
          ? {
              ...p,
              initialText: draftRes.text ?? "",
              attachments: draftRes.attachments,
              loadingDraft: false,
            }
          : p,
      );
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, loadingDraft: false, error: (err as Error).message } : p,
      );
    }
  }

  async function askNotesConfirm(message: string): Promise<boolean> {
    return await new Promise<boolean>((resolve) => {
      setNotesConfirm({ message, resolve });
    });
  }

  async function closeNotesPad(text: string): Promise<void> {
    if (
      pendingNotes &&
      (text.trim() || pendingNotes.attachments.length > 0) &&
      !(await askNotesConfirm("Discard current notes in the pad?"))
    )
      return;
    if (pendingNotes && (text.trim() || pendingNotes.attachments.length > 0)) {
      await api.clearChatNotesDraft(chat.id).catch(() => {});
      setSavedNotesDraft(null);
    }
    setPendingNotes(null);
  }

  async function send(): Promise<void> {
    const content = draft.trim();
    const hasAttachments = pendingAttachments.length > 0;
    if ((!content && !hasAttachments) || streaming) return;
    if (speech.listening) speech.stop();

    const cmd = content
      ? parseSlashCommand(content, skillCommands, CHAT_EXCLUDED_COMMANDS)
      : null;
    if (cmd && HANDLED_COMMANDS.has(cmd.name)) {
      setDraft("");
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

  function stop(): void {
    const partial = streamStore.pendingContent(streamKey).trim();
    stoppingRef.current = true;
    streamStore.stop(streamKey);
    void (async () => {
      try {
        if (partial) {
          await api.saveStoppedChatMessage(chat.id, `${partial}\n\n_(stopped)_`);
        }
      } catch {
        // best-effort
      } finally {
        try {
          setPersisted(await api.listChatMessages(chat.id));
        } catch {
          // ignore
        }
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
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4">
          <div className="relative mx-auto space-y-3" style={{ maxWidth: chatWidth }}>
            <ResizeHandle onMouseDown={onChatResize} />
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
                  onDelete={canDelete ? () => requestDeleteMessage(m) : undefined}
                />
              );
            })}
            {streaming && (
              <MessageBubble role="assistant" content={pendingContent} pending onStop={stop} />
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
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-lg border border-border bg-surface p-4 shadow-2xl">
            <div className="text-sm">{notesConfirm.message}</div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded border border-border px-3 py-1.5 text-xs hover:bg-bg"
                onClick={() => {
                  notesConfirm.resolve(false);
                  setNotesConfirm(null);
                }}
              >
                Cancel
              </button>
              <button
                className="rounded bg-accent px-3 py-1.5 text-xs text-white"
                onClick={() => {
                  notesConfirm.resolve(true);
                  setNotesConfirm(null);
                }}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
