import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import type { Chat, MeetingSession } from "../lib/types";
import { api } from "../lib/api";
import { ChatSessionPanel } from "./ChatSessionPanel";

interface Props {
  session: MeetingSession;
  /** The attached chat, or null until it's spawned. */
  chat: Chat | null;
  /** Report the chat that was created/updated/detached back to the parent. */
  onChat: (chat: Chat | null) => void;
}

/**
 * The "Ask assistant" tab: a full chat session grounded on the live meeting.
 * The chat is spawned + attached the first time this tab is opened (see the
 * backend `ensure_chat` + `live_chat_grounding`), then the standard
 * ChatSessionPanel is rendered — same composer, tools, attachments, model
 * picker and history as a regular chat.
 */
export function LiveChatSection({ session, chat, onChat }: Props) {
  const [error, setError] = useState<string | null>(null);
  const creatingRef = useRef(false);

  useEffect(() => {
    if (chat || creatingRef.current) return;
    creatingRef.current = true;
    let cancelled = false;
    void api
      .ensureMeetingChat(session.id)
      .then((c) => {
        if (!cancelled) onChat(c);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Couldn't start the chat.");
      })
      .finally(() => {
        creatingRef.current = false;
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat, session.id]);

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
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-sm text-muted">
      {error ? (
        <p className="text-red-500">{error}</p>
      ) : (
        <>
          <Loader2 size={18} className="animate-spin" />
          Starting the meeting assistant…
        </>
      )}
    </div>
  );
}
