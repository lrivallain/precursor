import type { Chat } from "../lib/types";
import { ChatSessionPanel } from "./ChatSessionPanel";

interface Props {
  chat: Chat;
  onChatUpdated: () => void;
  onArchived: () => void;
}

/**
 * The "Ask assistant" tab body: the standard ChatSessionPanel, grounded on the
 * live meeting via the backend (`live_chat_grounding`). The chat is spawned
 * explicitly from the toolbar's "Start Assistant" button — this component is
 * only rendered once one exists.
 */
export function LiveChatSection({ chat, onChatUpdated, onArchived }: Props) {
  return (
    <ChatSessionPanel chat={chat} onChatUpdated={onChatUpdated} onArchived={onArchived} />
  );
}
