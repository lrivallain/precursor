import { useEffect, useRef, useState } from "react";
import { Send, StopCircle } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { api } from "../lib/api";
import { streamChat } from "../lib/sse";
import type { Message, Topic } from "../lib/types";

interface Props {
  topic: Topic;
  onTopicUpdated: () => void;
}

export function ChatPanel({ topic, onTopicUpdated }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pendingContent, setPendingContent] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const msgs = await api.listMessages(topic.id);
      if (!cancelled) setMessages(msgs);
    })();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [topic.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pendingContent]);

  async function send(): Promise<void> {
    const content = draft.trim();
    if (!content || streaming) return;
    setDraft("");
    setStreaming(true);
    setPendingContent("");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        topic.id,
        { content },
        {
          signal: controller.signal,
          onEvent: (ev) => {
            const payload = JSON.parse(ev.data) as Record<string, unknown>;
            if (ev.event === "user_message") {
              setMessages((prev) => [
                ...prev,
                {
                  id: payload.id as number,
                  topic_id: topic.id,
                  role: "user",
                  content: payload.content as string,
                  tool_calls: null,
                  created_at: new Date().toISOString(),
                },
              ]);
            } else if (ev.event === "delta") {
              setPendingContent((c) => c + (payload.content as string));
            } else if (ev.event === "done") {
              setMessages((prev) => [
                ...prev,
                {
                  id: payload.id as number,
                  topic_id: topic.id,
                  role: "assistant",
                  content: payload.content as string,
                  tool_calls: null,
                  created_at: new Date().toISOString(),
                },
              ]);
              setPendingContent("");
              onTopicUpdated();
            } else if (ev.event === "error") {
              setMessages((prev) => [
                ...prev,
                {
                  id: -Date.now(),
                  topic_id: topic.id,
                  role: "system",
                  content: `Error: ${payload.message as string}`,
                  tool_calls: null,
                  created_at: new Date().toISOString(),
                },
              ]);
            }
          },
        },
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        console.error(err);
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function stop(): void {
    abortRef.current?.abort();
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && !streaming && (
          <div className="text-sm text-muted text-center pt-8">
            Send a message to start the conversation.
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} role={m.role} content={m.content} />
        ))}
        {streaming && (
          <MessageBubble role="assistant" content={pendingContent} pending />
        )}
      </div>

      <div className="border-t border-border p-3">
        <div className="max-w-3xl mx-auto flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="Type a message... (Shift+Enter for newline)"
            rows={2}
            className="flex-1 resize-none bg-surface border border-border rounded p-2 text-sm outline-none focus:border-accent"
          />
          {streaming ? (
            <button
              onClick={stop}
              className="px-3 py-2 rounded bg-surface border border-border hover:bg-bg"
              aria-label="Stop generation"
            >
              <StopCircle size={18} />
            </button>
          ) : (
            <button
              onClick={() => void send()}
              disabled={!draft.trim()}
              className="px-3 py-2 rounded bg-accent text-white disabled:opacity-40"
              aria-label="Send"
            >
              <Send size={18} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
