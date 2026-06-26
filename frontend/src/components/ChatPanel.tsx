import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRightCircle } from "lucide-react";
import { MessageBubble, AgentExchangeBadge } from "./MessageBubble";
import { SuggestedReplies } from "./SuggestedReplies";
import { ToolCallBubble } from "./ToolCallBubble";
import { CommandDraftCard, type CommandDraftPayload } from "./CommandDraftCard";
import { NotesPanel, type NotesAction } from "./NotesPanel";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { api } from "../lib/api";
import {
  splitSupportedAttachmentFiles,
  unsupportedAttachmentMessage,
} from "../lib/attachments";
import {
  commandsForSurface,
  GITHUB_SLASH_COMMANDS,
  formatMemoryList,
  matchSlashCommands,
  nextSyntheticMessageId,
  parseMemoryStoreArg,
  parseMemoryUpdateArg,
  parseSlashCommand,
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
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { ResizeHandle } from "./ResizeHandle";
import { useConfirm } from "./ConfirmDialog";
import { ReminderModal } from "./ReminderModal";
import { ReminderBanner } from "./ReminderBanner";
import type {
  AgentSession,
  Attachment,
  Message,
  NoteDraftAttachment,
  Reminder,
  Topic,
} from "../lib/types";

interface ChatPanelProps {
  topic: Topic;
  onTopicUpdated: () => void;
  /** Called after the topic is archived via the /archive command. */
  onArchived?: () => void;
  /** Switch the active topic (used by the /new command after creating one). */
  onNavigateTopic?: (topic: Topic) => void;
  /** Refresh the sidebar Reminders section after a set / cancel / done. */
  onRemindersChanged?: () => void;
  /** Persist a role change for this topic (null = default). */
  onSetRole?: (roleId: number | null) => Promise<void>;
  /** Open the header role selector (used by bare `/role`). */
  onOpenRoleSelector?: () => void;
}

type PendingKind = "gh-update" | "gh-create" | "gh-close";

interface PendingCommand {
  kind: PendingKind;
  loading: boolean;
  posting: boolean;
  body: string;
  title?: string;
  repo: string | null;
  issueNumber: number | null;
  error: string | null;
}

// Topic composer handles every built-in command. Derived from the catalog so
// it tracks SLASH_COMMANDS automatically (see lib/commands.ts).
const HANDLED_COMMANDS = commandsForSurface("topic");

function cardTitle(p: PendingCommand): string {
  switch (p.kind) {
    case "gh-update":
      return "Comment on GitHub issue";
    case "gh-create":
      return "Create GitHub issue";
    case "gh-close":
      return "Close GitHub issue";
  }
}

function cardSubtitle(p: PendingCommand): string | undefined {
  if (p.kind === "gh-create") return p.repo ?? undefined;
  if (p.repo && p.issueNumber) return `${p.repo}#${p.issueNumber}`;
  return undefined;
}

function cardSendLabel(kind: PendingKind): string {
  switch (kind) {
    case "gh-update":
      return "Post comment";
    case "gh-create":
      return "Create issue";
    case "gh-close":
      return "Close issue";
  }
}

function cardPostingLabel(kind: PendingKind): string {
  switch (kind) {
    case "gh-update":
      return "Posting…";
    case "gh-create":
      return "Creating…";
    case "gh-close":
      return "Closing…";
  }
}

function cardBodyPlaceholder(kind: PendingKind): string {
  return kind === "gh-close"
    ? "Optional closing comment in GitHub-Flavored Markdown… (leave empty to close without a comment)"
    : "Write in GitHub-Flavored Markdown…";
}

function cardConfirmHint(kind: PendingKind): string | undefined {
  if (kind === "gh-close") return "This will close the issue on GitHub.";
  if (kind === "gh-create") return "A new issue will be created and linked to this topic.";
  return undefined;
}

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

export function ChatPanel({ topic, onTopicUpdated, onArchived, onNavigateTopic, onRemindersChanged, onSetRole, onOpenRoleSelector }: ChatPanelProps) {
  const confirmAction = useConfirm();
  const [persisted, setPersisted] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [composerFocusToken, setComposerFocusToken] = useState(0);
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null);
  const [pendingNotes, setPendingNotes] = useState<PendingNotes | null>(null);
  const [savedNotesDraft, setSavedNotesDraft] = useState<{
    text: string;
    attachmentCount: number;
  } | null>(null);
  const [notesConfirm, setNotesConfirm] = useState<NotesConfirmState | null>(null);
  // One-shot reminder state for this topic (null = none set).
  const [reminder, setReminder] = useState<Reminder | null>(null);
  const [reminderModal, setReminderModal] = useState<{ note: string } | null>(null);
  const [reminderBusy, setReminderBusy] = useState(false);
  // Attachments uploaded by the user but not yet bound to a sent message.
  // They live as orphan rows server-side until either /messages/stream binds
  // them, or the user removes them / leaves the topic (in which case we DELETE
  // them so they don't accumulate as garbage).
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  // Messages the user removed but can still undo until the grace timer fires.
  // Each entry pairs the soft-deleted Message snapshot with a setTimeout id.
  const [pendingDeletes, setPendingDeletes] = useState<
    { message: Message; timer: number }[]
  >([]);
  const pendingAttachmentsRef = useRef<Attachment[]>([]);
  // Set while we handle a user-initiated Stop so the streaming→done effect
  // skips its own reload and lets stop() own the (post-persist) refresh.
  const stoppingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Subscribe to the global streaming store. The store owns the AbortController
  // and SSE handler so a stream survives switching topics.
  useStreamVersion();
  const settings = useSettings();
  const showStats = settings?.show_chat_stats ?? true;
  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;
  const agentsEnabled = settings?.agents_enabled ?? false;
  const excludedCommands = useMemo<ReadonlySet<string>>(() => {
    const set = new Set<string>(issueAssociationsEnabled ? [] : GITHUB_SLASH_COMMANDS);
    if (!agentsEnabled) set.add("agent");
    return set;
  }, [issueAssociationsEnabled, agentsEnabled]);
  const streamKey = convKey("topic", topic.id);
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

  // History recall (Up/Down to cycle through previous user messages).
  const historyIndexRef = useRef<number | null>(null);
  const originalDraftRef = useRef<string>("");
  const userHistory = useMemo(
    () => persisted.filter((m) => m.role === "user").map((m) => m.content),
    [persisted],
  );

  // Live speech-to-text via Azure (when configured server-side). Final chunks
  // are appended to the draft as the user speaks; the interim transcript is
  // shown transiently. The mic is hidden entirely when Azure isn't configured.
  const [interimText, setInterimText] = useState("");
  const appendFinalChunk = (text: string) => {
    const chunk = text.trim();
    if (!chunk) return;
    historyIndexRef.current = null;
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
  // Drop any lingering interim text once dictation stops.
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
          kind: "skill",
          argumentHint: "input",
        })),
    [skills],
  );

  const suggestions = useMemo<SlashCommand[]>(
    () => matchSlashCommands(draft, skillCommands, excludedCommands) ?? [],
    [draft, skillCommands, excludedCommands],
  );

  useEffect(() => {
    historyIndexRef.current = null;
    originalDraftRef.current = "";
  }, [topic.id]);

  // Keep a ref so cleanup effects can read the latest list without
  // re-subscribing every time it changes.
  useEffect(() => {
    pendingAttachmentsRef.current = pendingAttachments;
  }, [pendingAttachments]);

  // When the topic changes, drop unsent attachments + delete them server-side
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
  }, [topic.id]);

  // Flush any in-flight message deletions when the topic changes or the
  // panel unmounts: cancel the grace timer and commit the delete server-side
  // immediately so the next visit shows a consistent transcript.
  const pendingDeletesRef = useRef<typeof pendingDeletes>([]);
  useEffect(() => {
    pendingDeletesRef.current = pendingDeletes;
  }, [pendingDeletes]);
  useEffect(() => {
    const tid = topic.id;
    return () => {
      const queued = pendingDeletesRef.current;
      pendingDeletesRef.current = [];
      for (const p of queued) {
        window.clearTimeout(p.timer);
        void api.deleteMessage(tid, p.message.id).catch(() => {});
      }
    };
  }, [topic.id]);

  function commitDelete(messageId: number): void {
    setPendingDeletes((prev) => prev.filter((p) => p.message.id !== messageId));
    setPersisted((prev) => prev.filter((m) => m.id !== messageId));
    void api.deleteMessage(topic.id, messageId).catch(() => {});
  }

  function requestDeleteMessage(message: Message): void {
    // Don't queue the same message twice.
    if (pendingDeletesRef.current.some((p) => p.message.id === message.id))
      return;
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
          const att = await api.uploadAttachment(topic.id, file);
          setPendingAttachments((prev) => [...prev, att]);
        } catch (err) {
          setAttachmentError(
            (err as Error).message || "Upload failed",
          );
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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const msgs = await api.listMessages(topic.id);
      if (cancelled) return;
      setPersisted(msgs);
      // If we just switched into a topic whose session has already finished,
      // drop the buffered copy now that we have the canonical server state.
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
  }, [topic.id]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const res = await api.getNotesDraft(topic.id).catch(() => null);
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
  }, [topic.id]);

  // Load this topic's reminder (if any) so we can show the fired banner and
  // prefill the edit modal. Re-runs when the panel remounts after a fire.
  const refreshReminder = useMemo(
    () => async () => {
      try {
        setReminder(await api.getReminder("topic", topic.id));
      } catch {
        setReminder(null); // 404 => no reminder
      }
    },
    [topic.id],
  );
  useEffect(() => {
    void refreshReminder();
  }, [refreshReminder]);
  // refetch persisted messages and discard the buffered turn.
  const prevStreamingRef = useRef(streaming);
  useEffect(() => {
    prevStreamingRef.current = streamStore.isStreaming(streamKey);
  }, [topic.id]);
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
    (async () => {
      const msgs = await api.listMessages(topic.id);
      if (cancelled) return;
      setPersisted(msgs);
      streamStore.clear(streamKey);
      onTopicUpdated();
    })();
    return () => {
      cancelled = true;
    };
  }, [streaming, topic.id, onTopicUpdated]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pendingContent]);

  async function send(): Promise<void> {
    const content = draft.trim();
    const hasAttachments = pendingAttachments.length > 0;
    if ((!content && !hasAttachments) || streaming) return;
    historyIndexRef.current = null;
    if (speech.listening) speech.stop();

    const cmd = content
      ? parseSlashCommand(content, skillCommands, excludedCommands)
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
        // Persist the literal slash command as the user message;
        // the LLM receives the expanded prompt for this turn only.
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
    historyIndexRef.current = null;
    void streamStore.start(streamKey, text.trim());
  }

  function stop(): void {
    // Capture whatever the assistant has streamed so far, then cancel the
    // request. The backend only persists the *final* turn, which never runs
    // once we disconnect — so we save the partial reply ourselves instead of
    // letting it vanish. stoppingRef suppresses the streaming→done effect's
    // reload so this handler owns the post-persist refresh.
    const partial = streamStore.pendingContent(streamKey).trim();
    stoppingRef.current = true;
    streamStore.stop(streamKey);
    void (async () => {
      try {
        if (partial) {
          await api.saveStoppedMessage(topic.id, `${partial}\n\n_(stopped)_`);
        }
      } catch {
        // best-effort — keep going to refresh whatever did persist
      } finally {
        try {
          setPersisted(await api.listMessages(topic.id));
        } catch {
          // ignore — the topic may have changed underneath us
        }
        streamStore.clear(streamKey);
        onTopicUpdated();
      }
    })();
  }

  async function dispatchCommand(name: string, argument: string): Promise<void> {
    if (name === "gh-sync") {
      await runGhSync();
      return;
    }
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
        const draftRes = await api.getNotesDraft(topic.id);
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
      await runRename(argument);
      return;
    }
    if (name === "new") {
      await runNew(argument);
      return;
    }
    if (name === "pin" || name === "unpin") {
      await runSetPinned(name === "pin");
      return;
    }
    if (name === "clear") {
      await runClear();
      return;
    }
    if (name === "archive") {
      await runArchive();
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
      await runRole(argument);
      return;
    }
    if (name === "agent") {
      await runAgent(argument);
      return;
    }
    if (name === "gh-update" || name === "gh-create" || name === "gh-close") {
      await startDraft(name, argument);
    }
  }

  // Re-fetch the persisted transcript (the backend records reminder set/clear
  // confirmations as system messages, and our own SSE echo is suppressed).
  async function reloadPersisted(): Promise<void> {
    try {
      setPersisted(await api.listMessages(topic.id));
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
      await api.clearReminder("topic", topic.id);
      setReminder(null);
      await reloadPersisted();
      onRemindersChanged?.();
    } catch (err) {
      systemNote(`Reminder update failed: ${(err as Error).message}`);
    } finally {
      setReminderBusy(false);
    }
  }

  /** Append a local-only system note to the transcript (not persisted). */
  function systemNote(content: string): void {
    setPersisted((prev) => [
      ...prev,
      {
        id: nextSyntheticMessageId(),
        topic_id: topic.id,
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
        topic_id: topic.id,
        role: "user",
        content,
        tool_calls: null,
        created_at: new Date().toISOString(),
      },
    ]);
  }

  async function runMemoryStore(argument: string): Promise<void> {
    const parsed = parseMemoryStoreArg(argument);
    if (!parsed) return systemNote("Usage: `/memory-store [kind] <content>`");
    try {
      const mem = await api.createMemory(parsed);
      systemNote(`Saved memory #${mem.id} [${mem.kind}]. Manage in Settings → Memory.`);
    } catch (err) {
      systemNote(`Couldn't save memory: ${(err as Error).message}`);
    }
  }

  async function runMemoryList(): Promise<void> {
    try {
      const memories = await api.listMemories();
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
      const mem = await api.updateMemory(id, patch);
      systemNote(`Updated memory #${mem.id} [${mem.kind}].`);
    } catch (err) {
      systemNote(`Couldn't update memory #${id}: ${(err as Error).message}`);
    }
  }

  async function runRole(argument: string): Promise<void> {
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

  // Prefill the composer with "/agent <uuid> " so the user can type a follow-up
  // and reinstantiate an existing agent session straight from its summary.
  function prefillAgentFollowUp(ref: string): void {
    setDraft(`/agent ${ref} `);
    setComposerFocusToken((t) => t + 1);
  }

  // Navigate to the Agents tab. A non-null id opens that session; null opens
  // the new-agent form with this topic preselected (via the event's topicId).
  function openAgent(id: number | null): void {
    window.dispatchEvent(
      new CustomEvent("precursor:open-agent", { detail: { id, topicId: topic.id } }),
    );
  }

  async function runAgent(argument: string): Promise<void> {
    const arg = argument.trim();
    // "/agent <session-id> <prompt>" continues an existing session when the
    // first token is a session id — a public UUID (preferred) or a legacy
    // integer — that maps to a real agent. Anything else is treated as a brand
    // new task, so ordinary prompts still work.
    const m = arg.match(
      /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)\b\s*([\s\S]*)$/i,
    );
    if (m) {
      const ref = m[1];
      const prompt = m[2].trim();
      let existing: AgentSession | null = null;
      try {
        existing = await api.getAgent(ref);
      } catch {
        existing = null;
      }
      if (existing) {
        try {
          if (prompt) await api.sendToAgent(existing.id, prompt);
        } catch (err) {
          systemNote(`Couldn't message "${existing.title}": ${(err as Error).message}`);
          return;
        }
        systemNote(
          prompt
            ? `Sent a follow-up to "${existing.title}".`
            : `Opening "${existing.title}".`,
        );
        openAgent(existing.id);
        return;
      }
      // Not a real session id — fall through and treat the text as a new task.
    }

    if (!arg) {
      // No prompt: open the new-agent form with this topic preselected.
      openAgent(null);
      return;
    }
    try {
      const created = await api.createAgent({ task: arg, topic_id: topic.id });
      systemNote(`Started agent "${created.title}".`);
      openAgent(created.id);
    } catch (err) {
      systemNote(`Couldn't start the agent: ${(err as Error).message}`);
    }
  }

  async function runRename(argument: string): Promise<void> {
    const title = argument.trim();
    if (!title) {
      systemNote("Usage: `/rename <new title>`");
      return;
    }
    try {
      await api.updateTopic(topic.id, { title });
      onTopicUpdated();
    } catch (err) {
      systemNote(`Rename failed: ${(err as Error).message}`);
    }
  }

  async function runNew(argument: string): Promise<void> {
    const title = argument.trim();
    if (!title) {
      systemNote("Usage: `/new <title>`");
      return;
    }
    try {
      const created = await api.createTopic({ title, parent_id: topic.id });
      onNavigateTopic?.(created);
    } catch (err) {
      systemNote(`Create failed: ${(err as Error).message}`);
    }
  }

  async function runSetPinned(pinned: boolean): Promise<void> {
    if (topic.pinned === pinned) {
      systemNote(pinned ? "Already pinned." : "Not pinned.");
      return;
    }
    try {
      await api.updateTopic(topic.id, { pinned });
      onTopicUpdated();
    } catch (err) {
      systemNote(`${pinned ? "Pin" : "Unpin"} failed: ${(err as Error).message}`);
    }
  }

  async function runClear(): Promise<void> {
    if (
      !(await confirmAction({
        message: "Erase the entire chat transcript for this topic?",
        confirmLabel: "Erase transcript",
        variant: "danger",
      }))
    )
      return;
    try {
      await api.clearMessages(topic.id);
      setPersisted([]);
      onTopicUpdated();
    } catch (err) {
      systemNote(`Clear failed: ${(err as Error).message}`);
    }
  }

  async function runArchive(): Promise<void> {
    try {
      await api.archiveTopic(topic.id);
      onArchived?.();
    } catch (err) {
      systemNote(`Archive failed: ${(err as Error).message}`);
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
          const att = await api.uploadNoteAttachment(topic.id, file);
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
      await api.deleteNoteAttachment(topic.id, id);
    } catch {
      // ignore stale/deleted ids
    }
  }

  async function rephraseNotes(text: string): Promise<void> {
    if (!pendingNotes || !text.trim()) return;
    setPendingNotes((p) => (p ? { ...p, rephrasing: true, error: null } : p));
    try {
      const res = await api.rephraseNotes(topic.id, text);
      setPendingNotes((p) =>
        p ? { ...p, rephrasing: false, rephrasedText: res.text } : p,
      );
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, rephrasing: false, error: (err as Error).message } : p,
      );
    }
  }

  async function saveNotesDraft(text: string): Promise<void> {
    if (!pendingNotes) return;
    if (!text.trim() && pendingNotes.attachments.length === 0) return;
    if (!(await askNotesConfirm("Save notes as draft and close the pad?"))) return;
    setPendingNotes((p) => (p ? { ...p, savingDraft: true, error: null } : p));
    try {
      const res = await api.saveNotesDraft(topic.id, text.trim());
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

  async function runNotesAction(action: NotesAction, text: string): Promise<void> {
    if (!pendingNotes) return;
    const trimmed = text.trim();
    const attachmentIds = pendingNotes.attachments.map((a) => a.id);
    if (!trimmed && attachmentIds.length === 0) return;
    setPendingNotes((p) => (p ? { ...p, acting: true, error: null } : p));
    try {
      if (action === "append") {
        const res = await api.appendNotes(topic.id, trimmed, attachmentIds);
        await api.clearNotesDraft(topic.id).catch(() => {});
        setSavedNotesDraft(null);
        setPersisted((prev) => [...prev, res.message]);
        onTopicUpdated();
        setPendingNotes(null);
      } else if (action === "append-and-ask") {
        setSavedNotesDraft(null);
        setPendingNotes(null);
        const body = trimmed ? `**Notes**\n\n${trimmed}` : "**Notes**";
        void streamStore.start(streamKey, body, undefined, undefined, attachmentIds);
      } else if (action === "post-comment") {
        const res = await api.postGhUpdate(topic.id, trimmed, attachmentIds);
        await api.clearNotesDraft(topic.id).catch(() => {});
        setSavedNotesDraft(null);
        setPersisted((prev) => [
          ...prev,
          ...(res.local_note_message ? [res.local_note_message] : []),
          res.message,
        ]);
        onTopicUpdated();
        setPendingNotes(null);
      }
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, acting: false, error: (err as Error).message } : p,
      );
    }
  }

  async function discardSavedNotesDraft(): Promise<void> {
    if (!(await askNotesConfirm("Discard the saved notes draft?"))) return;
    try {
      await api.clearNotesDraft(topic.id);
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
      const draftRes = await api.getNotesDraft(topic.id);
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
      await api.clearNotesDraft(topic.id).catch(() => {});
      setSavedNotesDraft(null);
    }
    setPendingNotes(null);
  }

  async function runGhSync(): Promise<void> {
    // No editable card — sync is a fire-and-forget refresh. Show a transient
    // placeholder in the message list so the user gets immediate feedback.
    const placeholderId = -Date.now();
    setPersisted((prev) => [
      ...prev,
      {
        id: placeholderId,
        topic_id: topic.id,
        role: "system",
        content: "Syncing linked GitHub issue…",
        tool_calls: null,
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      const res = await api.syncGh(topic.id);
      setPersisted((prev) =>
        prev.filter((m) => m.id !== placeholderId).concat(res.message),
      );
      onTopicUpdated();
    } catch (err) {
      setPersisted((prev) =>
        prev.map((m) =>
          m.id === placeholderId
            ? { ...m, content: `Sync failed: ${(err as Error).message}` }
            : m,
        ),
      );
    }
  }

  async function startDraft(kind: PendingKind, argument: string): Promise<void> {
    const seed: PendingCommand = {
      kind,
      loading: true,
      posting: false,
      body: "",
      title: kind === "gh-create" ? "" : undefined,
      repo: null,
      issueNumber: null,
      error: null,
    };
    setPendingCommand(seed);

    try {
      if (kind === "gh-update") {
        const res = await api.draftGhUpdate(topic.id, argument || undefined);
        setPendingCommand((prev) =>
          prev?.kind === kind
            ? {
                ...prev,
                loading: false,
                body: res.draft,
                repo: res.repo,
                issueNumber: res.issue_number,
              }
            : prev,
        );
      } else if (kind === "gh-create") {
        const res = await api.draftGhCreate(topic.id, argument || undefined);
        setPendingCommand((prev) =>
          prev?.kind === kind
            ? {
                ...prev,
                loading: false,
                title: res.title,
                body: res.body,
                repo: res.repo,
              }
            : prev,
        );
      } else if (kind === "gh-close") {
        const res = await api.draftGhClose(topic.id, argument || undefined);
        setPendingCommand((prev) =>
          prev?.kind === kind
            ? {
                ...prev,
                loading: false,
                body: res.draft,
                repo: res.repo,
                issueNumber: res.issue_number,
              }
            : prev,
        );
      }
    } catch (err) {
      setPendingCommand((prev) =>
        prev?.kind === kind
          ? { ...prev, loading: false, error: (err as Error).message }
          : prev,
      );
    }
  }

  async function postCommandDraft(payload: CommandDraftPayload): Promise<void> {
    if (!pendingCommand) return;
    const kind = pendingCommand.kind;
    setPendingCommand((prev) =>
      prev ? { ...prev, posting: true, error: null } : prev,
    );
    try {
      let message: Message;
      if (kind === "gh-update") {
        const res = await api.postGhUpdate(topic.id, payload.body);
        message = res.message;
      } else if (kind === "gh-create") {
        const title = (payload.title ?? "").trim();
        if (!title) throw new Error("Title is required.");
        const res = await api.postGhCreate(topic.id, title, payload.body);
        message = res.message;
      } else {
        // gh-close
        const res = await api.postGhClose(topic.id, payload.body, "completed");
        message = res.message;
      }
      setPersisted((prev) => [...prev, message]);
      setPendingCommand(null);
      onTopicUpdated();
    } catch (err) {
      setPendingCommand((prev) =>
        prev?.kind === kind
          ? { ...prev, posting: false, error: (err as Error).message }
          : prev,
      );
    }
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
          <div
            className="relative mx-auto space-y-3"
            style={{ maxWidth: chatWidth }}
          >
            <ResizeHandle onMouseDown={onChatResize} />
          {visibleMessages.length === 0 && !streaming && (
            <div className="text-sm text-muted text-center pt-8">
              Send a message to start the conversation.
            </div>
          )}
          {(() => {
            const renderMessage = (m: (typeof visibleMessages)[number], grouped: boolean) => {
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
              // Hide assistant turns that only emitted tool calls (no text):
              // the tool bubbles below carry the meaningful content.
              if (m.role === "assistant" && !m.content.trim() && m.tool_calls) {
                return null;
              }
              const canDelete =
                !streaming && m.id > 0 && (m.role === "user" || m.role === "assistant");
              // In a scheduled topic the user turn is the repeated automation
              // prompt — collapse it so generated content gets the room.
              const collapsible = m.role === "user" && topic.kind === "scheduled";
              return (
                <MessageBubble
                  key={m.id}
                  role={m.role}
                  content={m.content}
                  attachments={m.attachments}
                  collapsible={collapsible}
                  agentSessionId={grouped ? undefined : m.agent_session_id}
                  createdAt={m.created_at}
                  model={m.model}
                  elapsedMs={m.elapsed_ms}
                  onDelete={canDelete ? () => requestDeleteMessage(m) : undefined}
                />
              );
            };

            // Wrap consecutive agent-tagged turns (prompt + answer) in a dashed
            // purple frame with a single AGENT badge, so an agent exchange reads
            // as one block instead of two separately-tagged bubbles.
            const out: ReactNode[] = [];
            let i = 0;
            while (i < visibleMessages.length) {
              const m = visibleMessages[i];
              const aid = m.agent_session_id;
              if (aid != null) {
                const group: typeof visibleMessages = [];
                let j = i;
                while (j < visibleMessages.length && visibleMessages[j].agent_session_id === aid) {
                  group.push(visibleMessages[j]);
                  j++;
                }
                // Prefer the agent's public UUID for the follow-up command;
                // fall back to the integer id for legacy rows without one.
                const agentRef = m.agent_session_public_id ?? String(aid);
                out.push(
                  <div
                    key={`agent-${aid}-${m.id}`}
                    className="space-y-3 rounded-lg border border-dashed border-purple-500/50 bg-purple-500/[0.03] p-2.5"
                  >
                    <AgentExchangeBadge agentSessionId={aid} />
                    {group.map((gm) => renderMessage(gm, true))}
                    {agentsEnabled && (
                      <div className="flex justify-end">
                        <button
                          type="button"
                          onClick={() => prefillAgentFollowUp(agentRef)}
                          className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-purple-500/40 px-2 py-0.5 text-[10px] font-medium text-purple-600 hover:bg-purple-500/10 dark:text-purple-300"
                          title="Continue this agent session"
                          data-tooltip="Continue this agent session"
                        >
                          <ArrowRightCircle size={11} />
                          Continue session
                        </button>
                      </div>
                    )}
                  </div>,
                );
                i = j;
              } else {
                out.push(renderMessage(m, false));
                i++;
              }
            }
            return out;
          })()}
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
                <UndoDeleteToast
                  key={p.message.id}
                  message={p.message}
                  onUndo={() => undoDelete(p.message.id)}
                />
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
              hasIssue={
                issueAssociationsEnabled && topic.github_issue_number !== null
              }
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
                        container: "topic",
                        containerId: topic.id,
                        title: `Notes — ${topic.title}`,
                        hasIssue:
                          issueAssociationsEnabled && topic.github_issue_number !== null,
                        allowPostComment: true,
                        initialText: text,
                        initialAttachments: pendingNotes.attachments,
                      });
                      setPendingNotes(null);
                    }
              }
            />
          )}
          {pendingCommand && (
            <CommandDraftCard
              title={cardTitle(pendingCommand)}
              subtitle={cardSubtitle(pendingCommand)}
              initialBody={pendingCommand.body}
              initialTitle={
                pendingCommand.kind === "gh-create" ? pendingCommand.title ?? "" : undefined
              }
              titleLabel="Issue title"
              bodyPlaceholder={cardBodyPlaceholder(pendingCommand.kind)}
              bodyRequired={pendingCommand.kind !== "gh-close"}
              loading={pendingCommand.loading}
              posting={pendingCommand.posting}
              error={pendingCommand.error}
              sendLabel={cardSendLabel(pendingCommand.kind)}
              postingLabel={cardPostingLabel(pendingCommand.kind)}
              confirmHint={cardConfirmHint(pendingCommand.kind)}
              onSend={postCommandDraft}
              onCancel={() => setPendingCommand(null)}
              onPopOut={
                pendingCommand.loading
                  ? undefined
                  : ({ body, title }) => {
                      const kind = pendingCommand.kind;
                      detachedDraftStore.open({
                        kind,
                        container: "topic",
                        containerId: topic.id,
                        title: cardTitle(pendingCommand),
                        subtitle: cardSubtitle(pendingCommand),
                        initialText: body,
                        initialTitle: title,
                        titleLabel: "Issue title",
                        bodyPlaceholder: cardBodyPlaceholder(kind),
                        bodyRequired: kind !== "gh-close",
                        sendLabel: cardSendLabel(kind),
                        postingLabel: cardPostingLabel(kind),
                        confirmHint: cardConfirmHint(kind),
                      });
                      setPendingCommand(null);
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
            focusToken={composerFocusToken}
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
          container="topic"
          containerId={topic.id}
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

const UNDO_DELETE_MS = 5000;

function UndoDeleteToast({
  message,
  onUndo,
}: {
  message: Message;
  onUndo: () => void;
}) {
  const [remaining, setRemaining] = useState(UNDO_DELETE_MS);
  useEffect(() => {
    const start = Date.now();
    const handle = window.setInterval(() => {
      const left = Math.max(0, UNDO_DELETE_MS - (Date.now() - start));
      setRemaining(left);
      if (left <= 0) window.clearInterval(handle);
    }, 100);
    return () => window.clearInterval(handle);
  }, []);
  const seconds = Math.ceil(remaining / 1000);
  const label = message.role === "user" ? "Your message" : "Assistant reply";
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-1.5 rounded border border-border bg-surface text-xs">
      <span className="text-muted truncate">
        {label} removed · undo in {seconds}s
      </span>
      <button
        type="button"
        onClick={onUndo}
        className="text-accent hover:underline shrink-0"
      >
        Undo
      </button>
    </div>
  );
}
