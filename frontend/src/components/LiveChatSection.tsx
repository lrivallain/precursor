import { useState } from "react";
import { Loader2, MessageSquare, Send } from "lucide-react";
import type { Chat, MeetingSession } from "../lib/types";
import { api } from "../lib/api";
import { streamStore, convKey } from "../lib/streamStore";
import { ChatSessionPanel } from "./ChatSessionPanel";

interface Props {
  session: MeetingSession;
  /** The attached chat, or null until the first ask spawns it. */
  chat: Chat | null;
  /** Report the chat that was created/updated/detached back to the parent. */
  onChat: (chat: Chat | null) => void;
}

/**
 * The "Ask assistant" tab, now a full chat session grounded on the live meeting.
 * On the first ask we spawn + attach a real Chat (see the backend
 * `ensure_chat` + `live_chat_grounding`) and render the standard ChatSessionPanel
 * — tools, attachments, history and all. Until then, a lightweight composer
 * seeds that first message.
 */
export function LiveChatSection({ session, chat, onChat }: Props) {
  const [draft, setDraft] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startChat(): Promise<void> {
    const content = draft.trim();
    if (!content || starting) return;
    setStarting(true);
    setError(null);
    try {
      const c = await api.ensureMeetingChat(session.id);
      onChat(c);
      setDraft("");
      // Stream the first message straight away; the panel that mounts next
      // picks up the in-flight session from the shared stream store.
      void streamStore.start(convKey("chat", c.id), content);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start the chat.");
    } finally {
      setStarting(false);
    }
  }

  if (chat) {
    return (
      <ChatSessionPanel
        chat={chat}
        onChatUpdated={() => {
          void api
            .getChat(chat.id)
            .then(onChat)
            .catch(() => {});
        }}
        onArchived={() => onChat(null)}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <MessageSquare size={22} className="opacity-70" aria-hidden="true" />
        <div>
          <p className="mb-1 text-sm font-medium text-text">Ask the meeting assistant</p>
          <p className="max-w-sm text-[13px] text-muted">
            Start a chat grounded on this meeting — the live transcript, insights, your notes
            and any attached topic. It&apos;s a full chat: tools, attachments and history
            included.
          </p>
        </div>
      </div>
      {error && <div className="px-3 pb-2 text-center text-[12px] text-red-500">{error}</div>}
      <div className="flex items-end gap-1.5 border-t border-border p-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void startChat();
            }
          }}
          rows={2}
          placeholder="Ask anything about the meeting…"
          className="min-h-[2.5rem] min-w-0 flex-1 resize-none rounded border border-border bg-surface px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
        <button
          type="button"
          onClick={() => void startChat()}
          disabled={starting || !draft.trim()}
          aria-label="Ask"
          className="rounded bg-accent p-2 text-white disabled:opacity-50"
        >
          {starting ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
        </button>
      </div>
    </div>
  );
}
