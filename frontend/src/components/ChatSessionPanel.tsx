import { useEffect, useMemo, useRef, useState } from "react";
import { MessageBubble } from "./MessageBubble";
import { ToolCallBubble } from "./ToolCallBubble";
import { NotesPanel, type NotesAction } from "./NotesPanel";
import { Composer } from "./Composer";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { ResizeHandle } from "./ResizeHandle";
import { api } from "../lib/api";
import {
  GITHUB_SLASH_COMMANDS,
  matchSlashCommands,
  parseSlashCommand,
  type SlashCommand,
} from "../lib/commands";
import { skillsStore, useSkills } from "../lib/skillsStore";
import { streamStore, useStreamVersion, convKey } from "../lib/streamStore";
import { useSettings } from "../lib/settingsStore";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import type { Attachment, Chat, Message } from "../lib/types";

interface ChatSessionPanelProps {
  chat: Chat;
  /** Refresh the chat list + active chat after a rename / pin / clear. */
  onChatUpdated: () => void;
  /** The chat was archived via /archive — drop the selection. */
  onArchived: () => void;
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
  rephrasing: boolean;
  acting: boolean;
  error: string | null;
  rephrasedText?: string;
}

export function ChatSessionPanel({
  chat,
  onChatUpdated,
  onArchived,
}: ChatSessionPanelProps) {
  const [persisted, setPersisted] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [pendingNotes, setPendingNotes] = useState<PendingNotes | null>(null);
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

  // When THIS chat's stream finishes, reload persisted and drop the buffer.
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
          const att = await api.uploadChatAttachment(chat.id, file);
          setPendingAttachments((prev) => [...prev, att]);
        } catch (err) {
          setAttachmentError((err as Error).message || "Upload failed");
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
      setPendingNotes({ rephrasing: false, acting: false, error: null });
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
      if (!window.confirm("Erase the entire transcript for this chat?")) return;
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
    if (!pendingNotes || !text.trim()) return;
    setPendingNotes((p) => (p ? { ...p, acting: true, error: null } : p));
    try {
      if (action === "append") {
        const res = await api.appendChatNotes(chat.id, text);
        setPersisted((prev) => [...prev, res.message]);
        onChatUpdated();
        setPendingNotes(null);
      } else if (action === "append-and-ask") {
        setPendingNotes(null);
        void streamStore.start(streamKey, `**Notes**\n\n${text.trim()}`);
      }
      // "post-comment" is GitHub-only and never offered for chats.
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, acting: false, error: (err as Error).message } : p,
      );
    }
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
      content || "(image attached)",
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
            {pendingNotes && (
              <NotesPanel
                hasIssue={false}
                allowPostComment={false}
                rephrasing={pendingNotes.rephrasing}
                acting={pendingNotes.acting}
                error={pendingNotes.error}
                rephrasedText={pendingNotes.rephrasedText}
                onRephrase={rephraseNotes}
                onAction={runNotesAction}
                onCancel={() => setPendingNotes(null)}
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
    </div>
  );
}
