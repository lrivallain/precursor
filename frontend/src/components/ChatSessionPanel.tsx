import { useEffect, useMemo, useRef, useState } from "react";
import { Mic, Send, StopCircle } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { ToolCallBubble } from "./ToolCallBubble";
import { NotesPanel, type NotesAction } from "./NotesPanel";
import { SlashCommandPicker } from "./SlashCommandPicker";
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
import type { Chat, Message } from "../lib/types";

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
  const [pickerIndex, setPickerIndex] = useState(0);
  const [pendingDeletes, setPendingDeletes] = useState<
    { message: Message; timer: number }[]
  >([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stoppingRef = useRef(false);
  const historyIndexRef = useRef<number | null>(null);
  const originalDraftRef = useRef<string>("");

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
  const pickerOpen = suggestions.length > 0;

  const userHistory = useMemo(
    () => persisted.filter((m) => m.role === "user").map((m) => m.content),
    [persisted],
  );

  useEffect(() => setPickerIndex(0), [draft]);
  useEffect(() => {
    historyIndexRef.current = null;
    originalDraftRef.current = "";
  }, [chat.id]);

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
    if (!content || streaming) return;
    historyIndexRef.current = null;
    if (speech.listening) speech.stop();

    const cmd = parseSlashCommand(content, skillCommands, CHAT_EXCLUDED_COMMANDS);
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
        void streamStore.start(streamKey, content, expanded);
        return;
      }
    }
    setDraft("");
    void streamStore.start(streamKey, content);
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

  function selectCommand(cmd: SlashCommand): void {
    setDraft(`/${cmd.name} `);
    textareaRef.current?.focus();
  }

  function recallHistory(dir: -1 | 1): void {
    if (userHistory.length === 0) return;
    const cur = historyIndexRef.current;
    if (dir === -1) {
      if (cur === null) {
        originalDraftRef.current = draft;
        historyIndexRef.current = userHistory.length - 1;
      } else if (cur > 0) {
        historyIndexRef.current = cur - 1;
      }
    } else {
      if (cur === null) return;
      if (cur < userHistory.length - 1) {
        historyIndexRef.current = cur + 1;
      } else {
        historyIndexRef.current = null;
        setDraft(originalDraftRef.current);
        return;
      }
    }
    const idx = historyIndexRef.current;
    if (idx !== null) setDraft(userHistory[idx]);
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
                rephrasing={pendingNotes.rephrasing}
                acting={pendingNotes.acting}
                error={pendingNotes.error}
                rephrasedText={pendingNotes.rephrasedText}
                onRephrase={rephraseNotes}
                onAction={runNotesAction}
                onCancel={() => setPendingNotes(null)}
              />
            )}
            {speech.listening && (
              <div className="flex items-start gap-2 text-[11px] text-muted px-1">
                <span className="inline-block h-2 w-2 mt-1 shrink-0 rounded-full bg-red-500 animate-pulse" />
                <span className="min-w-0 break-words max-h-20 overflow-y-auto">
                  Listening… {interimText && <span className="italic">{interimText}</span>}
                </span>
              </div>
            )}
            {speech.error && (
              <div className="text-[11px] text-red-500 px-1">Dictation error: {speech.error}</div>
            )}
            <div className="relative flex items-end gap-2">
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
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => {
                  historyIndexRef.current = null;
                  setDraft(e.target.value);
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
                      setPickerIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
                      return;
                    }
                    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && !e.altKey)) {
                      e.preventDefault();
                      selectCommand(suggestions[pickerIndex]);
                      return;
                    }
                  }
                  if (
                    (e.key === "ArrowUp" || e.key === "ArrowDown") &&
                    !e.shiftKey &&
                    textareaRef.current &&
                    textareaRef.current.selectionStart === textareaRef.current.selectionEnd &&
                    (e.key === "ArrowUp"
                      ? textareaRef.current.selectionStart === 0
                      : textareaRef.current.selectionStart === draft.length)
                  ) {
                    e.preventDefault();
                    recallHistory(e.key === "ArrowUp" ? -1 : 1);
                    return;
                  }
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                rows={1}
                placeholder={`Message ${chat.title}… (/ for commands)`}
                className="flex-1 resize-none rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
                style={{ height: composerHeight }}
              />
              {azureReady && (
                <button
                  className={`p-2 rounded shrink-0 ${
                    speech.listening ? "bg-red-500/15 text-red-500" : "hover:bg-surface"
                  }`}
                  aria-label={speech.listening ? "Stop dictation" : "Start dictation"}
                  data-tooltip={speech.listening ? "Stop dictation" : "Dictate"}
                  onClick={() => (speech.listening ? speech.stop() : speech.start())}
                >
                  <Mic size={18} />
                </button>
              )}
              {streaming ? (
                <button
                  className="flex items-center gap-1 rounded bg-surface px-3 py-2 text-sm hover:bg-border shrink-0"
                  onClick={stop}
                  aria-label="Stop generating"
                >
                  <StopCircle size={16} /> Stop
                </button>
              ) : (
                <button
                  className="flex items-center gap-1 rounded bg-accent px-3 py-2 text-sm text-white disabled:opacity-50 shrink-0"
                  onClick={() => void send()}
                  disabled={!draft.trim()}
                  aria-label="Send message"
                >
                  <Send size={16} /> Send
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {showStats && <ChatStatsPanel streamKey={streamKey} messages={messages} />}
    </div>
  );
}
