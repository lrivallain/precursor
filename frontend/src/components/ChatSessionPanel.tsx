import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Send, StopCircle } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { ToolCallBubble } from "./ToolCallBubble";
import { ResizeHandle } from "./ResizeHandle";
import { api } from "../lib/api";
import { streamChatSession, type SSEEvent } from "../lib/sse";
import { useResizableWidth } from "../lib/useResizableWidth";
import type { Chat, Message } from "../lib/types";

interface ChatSessionPanelProps {
  chat: Chat;
  /** Notify the parent so it can refresh the chat list (unread, updated_at). */
  onActivity?: () => void;
}

interface ParsedToolMeta {
  tool_call_id?: string;
  name?: string;
  arguments?: string;
  is_error?: boolean;
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

// Transient tool bubble shown while a turn is streaming (before the persisted
// rows are reloaded from the server on completion).
interface LiveTool {
  toolCallId: string;
  name: string;
  arguments: string;
  content: string | null;
  isError: boolean;
}

export function ChatSessionPanel({ chat, onActivity }: ChatSessionPanelProps) {
  const [persisted, setPersisted] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pendingAssistant, setPendingAssistant] = useState("");
  const [liveTools, setLiveTools] = useState<LiveTool[]>([]);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const { width: chatWidth, onMouseDown: onChatResize } = useResizableWidth({
    storageKey: "precursor:chat:width",
    defaultWidth: 768,
    min: 480,
    max: 1400,
  });

  // Load persisted history whenever the active chat changes.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const msgs = await api.listChatMessages(chat.id);
        if (!cancelled) setPersisted(msgs);
      } catch {
        if (!cancelled) setPersisted([]);
      }
    })();
    // Reset transient state for the new chat.
    setDraft("");
    setPendingAssistant("");
    setLiveTools([]);
    setError(null);
    return () => {
      cancelled = true;
    };
  }, [chat.id]);

  // Autoscroll to the bottom as content arrives.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [persisted, pendingAssistant, liveTools]);

  const reload = useCallback(async (): Promise<void> => {
    try {
      setPersisted(await api.listChatMessages(chat.id));
    } catch {
      // ignore — the chat may have changed underneath us
    }
  }, [chat.id]);

  const handleEvent = useCallback((evt: SSEEvent): void => {
    let data: Record<string, unknown> = {};
    try {
      data = JSON.parse(evt.data) as Record<string, unknown>;
    } catch {
      data = {};
    }
    switch (evt.event) {
      case "delta":
        setPendingAssistant((prev) => prev + String(data.content ?? ""));
        break;
      case "tool_calls": {
        const calls = (data.calls as Array<Record<string, unknown>>) ?? [];
        setLiveTools((prev) => [
          ...prev,
          ...calls.map((c) => ({
            toolCallId: String(c.id ?? ""),
            name: String(c.name ?? ""),
            arguments: String(c.arguments ?? "{}"),
            content: null,
            isError: false,
          })),
        ]);
        // The assistant text that preceded these calls is now committed
        // server-side; clear the transient buffer for the next round.
        setPendingAssistant("");
        break;
      }
      case "tool_result": {
        const id = String(data.tool_call_id ?? "");
        setLiveTools((prev) =>
          prev.map((t) =>
            t.toolCallId === id
              ? {
                  ...t,
                  content: String(data.content ?? ""),
                  isError: Boolean(data.is_error),
                }
              : t,
          ),
        );
        break;
      }
      case "system":
        // Non-fatal notice (e.g. an MCP server was unavailable).
        break;
      case "error":
        setError(String(data.message ?? "Something went wrong."));
        break;
      default:
        break;
    }
  }, []);

  const send = useCallback(async (): Promise<void> => {
    const content = draft.trim();
    if (!content || streaming) return;
    setDraft("");
    setError(null);
    setStreaming(true);
    setPendingAssistant("");
    setLiveTools([]);

    // Optimistically echo the user turn so it appears immediately.
    const optimistic: Message = {
      id: -Date.now(),
      topic_id: null,
      chat_id: chat.id,
      role: "user",
      content,
      tool_calls: null,
      created_at: new Date().toISOString(),
    };
    setPersisted((prev) => [...prev, optimistic]);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChatSession(
        chat.id,
        { content },
        { signal: controller.signal, onEvent: handleEvent },
      );
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setStreaming(false);
      setPendingAssistant("");
      setLiveTools([]);
      abortRef.current = null;
      await reload();
      onActivity?.();
    }
  }, [draft, streaming, chat.id, handleEvent, reload, onActivity]);

  function stop(): void {
    const partial = pendingAssistant.trim();
    abortRef.current?.abort();
    void (async () => {
      try {
        if (partial) {
          await api.saveStoppedChatMessage(chat.id, `${partial}\n\n_(stopped)_`);
        }
      } catch {
        // best-effort
      } finally {
        await reload();
      }
    })();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  const visibleMessages = useMemo(
    () => persisted.filter((m) => !(m.role === "assistant" && !m.content.trim() && m.tool_calls)),
    [persisted],
  );

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="relative mx-auto space-y-4 px-4 py-4" style={{ maxWidth: chatWidth }}>
          <ResizeHandle onMouseDown={onChatResize} />
          {visibleMessages.length === 0 && !streaming && (
            <div className="pt-8 text-center text-sm text-muted">
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
                  content={m.content}
                  isError={Boolean(meta?.is_error)}
                />
              );
            }
            return (
              <MessageBubble key={m.id} role={m.role} content={m.content} attachments={m.attachments} />
            );
          })}
          {/* Live tool bubbles for the in-flight turn. */}
          {streaming &&
            liveTools.map((t) => (
              <ToolCallBubble
                key={t.toolCallId}
                name={t.name}
                arguments={t.arguments}
                content={t.content}
                isError={t.isError}
                pending={t.content === null}
              />
            ))}
          {streaming && (
            <MessageBubble role="assistant" content={pendingAssistant} pending onStop={stop} />
          )}
          {error && (
            <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-400">
              {error}
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-border p-3">
        <div className="mx-auto flex items-end gap-2" style={{ maxWidth: chatWidth }}>
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={`Message ${chat.title}…`}
            className="flex-1 resize-none rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            style={{ minHeight: 40, maxHeight: 240 }}
          />
          {streaming ? (
            <button
              className="flex items-center gap-1 rounded bg-surface px-3 py-2 text-sm hover:bg-border"
              onClick={stop}
              aria-label="Stop generating"
            >
              <StopCircle size={16} /> Stop
            </button>
          ) : (
            <button
              className="flex items-center gap-1 rounded bg-accent px-3 py-2 text-sm text-white disabled:opacity-50"
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
  );
}
