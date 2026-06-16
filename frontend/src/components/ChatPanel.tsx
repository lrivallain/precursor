import { useEffect, useMemo, useRef, useState } from "react";
import { Mic, Paperclip, Send, StopCircle, X } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { ToolCallBubble } from "./ToolCallBubble";
import { CommandDraftCard, type CommandDraftPayload } from "./CommandDraftCard";
import { NotesPanel, type NotesAction } from "./NotesPanel";
import { SlashCommandPicker } from "./SlashCommandPicker";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { api } from "../lib/api";
import {
  GITHUB_SLASH_COMMANDS,
  matchSlashCommands,
  parseSlashCommand,
  type SlashCommand,
} from "../lib/commands";
import { skillsStore, useSkills } from "../lib/skillsStore";
import { streamStore, useStreamVersion } from "../lib/streamStore";
import { useSettings } from "../lib/settingsStore";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useSpeechRecognition } from "../lib/useSpeechRecognition";
import { ResizeHandle } from "./ResizeHandle";
import type { Attachment, Message, Topic } from "../lib/types";

interface ChatPanelProps {
  topic: Topic;
  onTopicUpdated: () => void;
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

const HANDLED_COMMANDS = new Set<string>([
  "gh-update",
  "gh-sync",
  "gh-create",
  "gh-close",
  "notes",
]);

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
  rephrasing: boolean;
  acting: boolean;
  error: string | null;
  rephrasedText?: string;
}

export function ChatPanel({ topic, onTopicUpdated }: ChatPanelProps) {
  const [persisted, setPersisted] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null);
  const [pendingNotes, setPendingNotes] = useState<PendingNotes | null>(null);
  const [pickerIndex, setPickerIndex] = useState(0);
  // Attachments uploaded by the user but not yet bound to a sent message.
  // They live as orphan rows server-side until either /messages/stream binds
  // them, or the user removes them / leaves the topic (in which case we DELETE
  // them so they don't accumulate as garbage).
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  // Messages the user removed but can still undo until the grace timer fires.
  // Each entry pairs the soft-deleted Message snapshot with a setTimeout id.
  const [pendingDeletes, setPendingDeletes] = useState<
    { message: Message; timer: number }[]
  >([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
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
  const excludedCommands = useMemo<ReadonlySet<string>>(
    () => (issueAssociationsEnabled ? new Set<string>() : GITHUB_SLASH_COMMANDS),
    [issueAssociationsEnabled],
  );
  const streaming = streamStore.isStreaming(topic.id);
  const pendingContent = streamStore.pendingContent(topic.id);
  const buffered = streamStore.bufferedMessages(topic.id);
  const hasSession = streamStore.hasSession(topic.id);
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

  // Live speech-to-text (Web Speech API). Final chunks are appended to the
  // draft as the user speaks; the interim transcript is shown transiently as a
  // dim suffix so they see words land in real time before they're committed.
  const [interimText, setInterimText] = useState("");
  const speech = useSpeechRecognition({
    onFinalChunk: (text) => {
      const chunk = text.trim();
      if (!chunk) return;
      historyIndexRef.current = null;
      setDraft((d) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk));
      setInterimText("");
    },
    onInterim: setInterimText,
  });
  // Drop any lingering interim text once dictation stops.
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
        kind: "skill",
        argumentHint: "input",
      })),
    [skills],
  );

  const suggestions = useMemo<SlashCommand[]>(
    () => matchSlashCommands(draft, skillCommands, excludedCommands) ?? [],
    [draft, skillCommands, excludedCommands],
  );
  const pickerOpen = suggestions.length > 0;

  useEffect(() => {
    setPickerIndex(0);
  }, [draft]);

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
    const images: File[] = [];
    for (const f of files) {
      if (f && f.type && f.type.startsWith("image/")) images.push(f);
    }
    if (images.length === 0) return;
    setAttachmentError(null);
    setUploadingCount((n) => n + images.length);
    try {
      for (const file of images) {
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
      setUploadingCount((n) => Math.max(0, n - images.length));
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
        streamStore.hasSession(topic.id) &&
        !streamStore.isStreaming(topic.id)
      ) {
        streamStore.clear(topic.id);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [topic.id]);

  // When the stream for THIS topic transitions from streaming to done,
  // refetch persisted messages and discard the buffered turn.
  const prevStreamingRef = useRef(streaming);
  useEffect(() => {
    prevStreamingRef.current = streamStore.isStreaming(topic.id);
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
      streamStore.clear(topic.id);
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
        void streamStore.start(topic.id, content, expanded, atts);
        return;
      }
    }

    setDraft("");
    const atts = pendingAttachments;
    setPendingAttachments([]);
    void streamStore.start(
      topic.id,
      content || "(image attached)",
      undefined,
      atts,
    );
  }

  function stop(): void {
    // Capture whatever the assistant has streamed so far, then cancel the
    // request. The backend only persists the *final* turn, which never runs
    // once we disconnect — so we save the partial reply ourselves instead of
    // letting it vanish. stoppingRef suppresses the streaming→done effect's
    // reload so this handler owns the post-persist refresh.
    const partial = streamStore.pendingContent(topic.id).trim();
    stoppingRef.current = true;
    streamStore.stop(topic.id);
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
        streamStore.clear(topic.id);
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
      setPendingNotes({ rephrasing: false, acting: false, error: null });
      return;
    }
    if (name === "gh-update" || name === "gh-create" || name === "gh-close") {
      await startDraft(name, argument);
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

  async function runNotesAction(action: NotesAction, text: string): Promise<void> {
    if (!pendingNotes || !text.trim()) return;
    setPendingNotes((p) => (p ? { ...p, acting: true, error: null } : p));
    try {
      if (action === "append") {
        const res = await api.appendNotes(topic.id, text);
        setPersisted((prev) => [...prev, res.message]);
        onTopicUpdated();
        setPendingNotes(null);
      } else if (action === "append-and-ask") {
        setPendingNotes(null);
        void streamStore.start(topic.id, `**Notes**\n\n${text.trim()}`);
      } else if (action === "post-comment") {
        const res = await api.postGhUpdate(topic.id, text);
        setPersisted((prev) => [...prev, res.message]);
        onTopicUpdated();
        setPendingNotes(null);
      }
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, acting: false, error: (err as Error).message } : p,
      );
    }
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

  function selectCommand(cmd: SlashCommand): void {
    // Insert the command name + a trailing space so the picker dismisses
    // and the user can start typing the argument immediately.
    setDraft(`/${cmd.name} `);
    textareaRef.current?.focus();
  }

  return (
    <div className="h-full flex min-h-0">
      <div className="flex-1 flex flex-col min-h-0">
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
            // Hide assistant turns that only emitted tool calls (no text):
            // the tool bubbles below carry the meaningful content.
            if (m.role === "assistant" && !m.content.trim() && m.tool_calls) {
              return null;
            }
            const canDelete =
              !streaming &&
              m.id > 0 &&
              (m.role === "user" || m.role === "assistant");
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
                <UndoDeleteToast
                  key={p.message.id}
                  message={p.message}
                  onUndo={() => undoDelete(p.message.id)}
                />
              ))}
            </div>
          )}
          {pendingNotes && (
            <NotesPanel
              hasIssue={
                issueAssociationsEnabled && topic.github_issue_number !== null
              }
              rephrasing={pendingNotes.rephrasing}
              acting={pendingNotes.acting}
              error={pendingNotes.error}
              rephrasedText={pendingNotes.rephrasedText}
              onRephrase={rephraseNotes}
              onAction={runNotesAction}
              onCancel={() => setPendingNotes(null)}
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
              bodyPlaceholder={
                pendingCommand.kind === "gh-close"
                  ? "Optional closing comment in GitHub-Flavored Markdown… (leave empty to close without a comment)"
                  : "Write in GitHub-Flavored Markdown…"
              }
              bodyRequired={pendingCommand.kind !== "gh-close"}
              loading={pendingCommand.loading}
              posting={pendingCommand.posting}
              error={pendingCommand.error}
              sendLabel={cardSendLabel(pendingCommand.kind)}
              postingLabel={cardPostingLabel(pendingCommand.kind)}
              confirmHint={
                pendingCommand.kind === "gh-close"
                  ? "This will close the issue on GitHub."
                  : pendingCommand.kind === "gh-create"
                    ? "A new issue will be created and linked to this topic."
                    : undefined
              }
              onSend={postCommandDraft}
              onCancel={() => setPendingCommand(null)}
            />
          )}
          {(pendingAttachments.length > 0 ||
            uploadingCount > 0 ||
            attachmentError) && (
            <div className="flex flex-wrap items-center gap-2">
              {pendingAttachments.map((a) => (
                <AttachmentChip
                  key={a.id}
                  attachment={a}
                  onRemove={() => void removeAttachment(a.id)}
                />
              ))}
              {uploadingCount > 0 && (
                <span className="text-[11px] text-muted italic px-2 py-1">
                  Uploading {uploadingCount}…
                </span>
              )}
              {attachmentError && (
                <span className="text-[11px] text-red-500 px-2 py-1">
                  {attachmentError}
                </span>
              )}
            </div>
          )}
          {speech.listening && (
            <div className="flex items-center gap-2 text-[11px] text-muted px-1">
              <span className="inline-block h-2 w-2 rounded-full bg-red-500 animate-pulse" />
              <span className="truncate">
                Listening… {interimText && <span className="italic">{interimText}</span>}
              </span>
            </div>
          )}
          {speech.error && (
            <div className="text-[11px] text-red-500 px-1">Dictation error: {speech.error}</div>
          )}
          <div
            className={`relative flex items-end gap-2 ${
              isDraggingFile
                ? "ring-2 ring-accent/60 rounded-md"
                : ""
            }`}
            onDragOver={(e) => {
              if (e.dataTransfer.types.includes("Files")) {
                e.preventDefault();
                setIsDraggingFile(true);
              }
            }}
            onDragLeave={(e) => {
              // Ignore drag-leave bubbling from inner children.
              if (
                !e.currentTarget.contains(e.relatedTarget as Node | null)
              ) {
                setIsDraggingFile(false);
              }
            }}
            onDrop={(e) => {
              if (e.dataTransfer.types.includes("Files")) {
                e.preventDefault();
                setIsDraggingFile(false);
                void uploadFiles(e.dataTransfer.files);
              }
            }}
          >
            <div
              role="separator"
              aria-orientation="horizontal"
              onMouseDown={onComposerResize}
              title="Drag to resize"
              className="absolute -top-2 left-0 right-0 h-2 cursor-row-resize group z-10"
            >
              <div className="h-px w-12 mx-auto mt-1 bg-border group-hover:bg-accent/60 transition-colors" />
            </div>
            {pickerOpen && (
              <SlashCommandPicker
                commands={suggestions}
                activeIndex={pickerIndex}
                onSelect={selectCommand}
                onHover={setPickerIndex}
              />
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) void uploadFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => {
                historyIndexRef.current = null;
                setDraft(e.target.value);
              }}
              onPaste={(e) => {
                const items = e.clipboardData?.items;
                if (!items) return;
                const files: File[] = [];
                for (const it of items) {
                  if (it.kind === "file") {
                    const f = it.getAsFile();
                    if (f && f.type.startsWith("image/")) files.push(f);
                  }
                }
                if (files.length > 0) {
                  e.preventDefault();
                  void uploadFiles(files);
                }
              }}
              onKeyDown={(e) => {
                if (pickerOpen) {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setPickerIndex((i) => (i + 1) % suggestions.length);
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setPickerIndex(
                      (i) => (i - 1 + suggestions.length) % suggestions.length,
                    );
                    return;
                  }
                  if (
                    e.key === "Tab" ||
                    (e.key === "Enter" && !e.shiftKey && !e.altKey)
                  ) {
                    e.preventDefault();
                    selectCommand(suggestions[pickerIndex]);
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setDraft("");
                    return;
                  }
                }
                if (e.key === "ArrowUp" && userHistory.length > 0) {
                  const ta = e.currentTarget;
                  const caretAtTop =
                    historyIndexRef.current !== null ||
                    !ta.value.slice(0, ta.selectionStart).includes("\n");
                  if (caretAtTop) {
                    e.preventDefault();
                    if (historyIndexRef.current === null) {
                      originalDraftRef.current = draft;
                      historyIndexRef.current = userHistory.length - 1;
                    } else if (historyIndexRef.current > 0) {
                      historyIndexRef.current -= 1;
                    }
                    setDraft(userHistory[historyIndexRef.current] ?? "");
                    return;
                  }
                }
                if (e.key === "ArrowDown" && historyIndexRef.current !== null) {
                  const ta = e.currentTarget;
                  const caretAtBottom = !ta.value
                    .slice(ta.selectionStart)
                    .includes("\n");
                  if (caretAtBottom) {
                    e.preventDefault();
                    const next = historyIndexRef.current + 1;
                    if (next >= userHistory.length) {
                      historyIndexRef.current = null;
                      setDraft(originalDraftRef.current);
                    } else {
                      historyIndexRef.current = next;
                      setDraft(userHistory[next]);
                    }
                    return;
                  }
                }
                if (e.key === "Enter") {
                  if (e.altKey) {
                    // Option/Alt+Enter inserts a newline at the caret.
                    // Browsers don't do this natively for textareas, so we
                    // insert manually and keep history navigation working.
                    e.preventDefault();
                    const ta = e.currentTarget;
                    const start = ta.selectionStart;
                    const end = ta.selectionEnd;
                    const next =
                      ta.value.slice(0, start) + "\n" + ta.value.slice(end);
                    setDraft(next);
                    requestAnimationFrame(() => {
                      ta.selectionStart = ta.selectionEnd = start + 1;
                    });
                    historyIndexRef.current = null;
                    return;
                  }
                  if (!e.shiftKey) {
                    e.preventDefault();
                    historyIndexRef.current = null;
                    void send();
                  }
                  // Shift+Enter falls through to default textarea newline.
                }
              }}
              placeholder="Type a message or /command... (Shift/Option+Enter for newline, ↑/↓ for history)"
              style={{ height: composerHeight }}
              className="flex-1 resize-none bg-surface border border-border rounded p-2 text-sm outline-none focus:border-accent"
            />
            <div
              className={`flex gap-2 ${
                composerHeight >= 96 ? "flex-col" : "flex-row items-end"
              }`}
            >
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="px-2 py-2 rounded bg-surface border border-border text-muted hover:text-text hover:bg-bg"
                aria-label="Attach image"
                data-tooltip="Attach image (or paste / drop)"
              >
                <Paperclip size={18} />
              </button>
              {speech.supported && (
                <button
                  type="button"
                  onClick={speech.toggle}
                  className={`px-2 py-2 rounded border border-border ${
                    speech.listening
                      ? "bg-accent text-white animate-pulse"
                      : "bg-surface text-muted hover:text-text hover:bg-bg"
                  }`}
                  aria-label={speech.listening ? "Stop dictation" : "Dictate"}
                  aria-pressed={speech.listening}
                  data-tooltip={
                    speech.listening ? "Stop dictation" : "Dictate (speech-to-text)"
                  }
                >
                  <Mic size={18} />
                </button>
              )}
              {streaming ? (
                <button
                  onClick={stop}
                  className="px-3 py-2 rounded bg-surface border border-border hover:bg-bg"
                  aria-label="Stop generation"
                  data-tooltip="Stop generation"
                >
                  <StopCircle size={18} />
                </button>
              ) : (
                <button
                  onClick={() => void send()}
                  disabled={!draft.trim() && pendingAttachments.length === 0}
                  className="px-3 py-2 rounded bg-accent text-white disabled:opacity-40"
                  aria-label="Send"
                  data-tooltip="Send (Enter)"
                >
                  <Send size={18} />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      </div>
      {showStats && <ChatStatsPanel topicId={topic.id} messages={messages} />}
    </div>
  );
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}) {
  const label = attachment.original_filename || `image-${attachment.id}`;
  return (
    <div className="flex items-center gap-2 pl-1 pr-2 py-1 rounded border border-border bg-surface text-xs max-w-[14rem]">
      <img
        src={api.attachmentUrl(attachment.id)}
        alt=""
        className="w-8 h-8 rounded object-cover border border-border shrink-0"
      />
      <span className="truncate" title={label}>
        {label}
      </span>
      <button
        type="button"
        onClick={onRemove}
        className="p-0.5 rounded text-muted hover:text-text hover:bg-bg shrink-0"
        aria-label={`Remove ${label}`}
        data-tooltip="Remove"
      >
        <X size={12} />
      </button>
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
