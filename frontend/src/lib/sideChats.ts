/**
 * Side chats: chats started from a topic, or from one of its replies.
 *
 * Starting one and following its links crosses sections (topic ⇄ chat), which
 * the section controllers own. These window events let any component ask for
 * the switch, as `precursor:open-agent` does for agents.
 */

import { api } from "./api";
import type { Chat } from "./types";

export const OPEN_CHAT_EVENT = "precursor:open-chat";
export const OPEN_TOPIC_EVENT = "precursor:open-topic";

export interface OpenChatDetail {
  chat: Chat;
}

export interface OpenTopicDetail {
  topicId: number;
}

/** Switch to the Chats section and open `chat`. */
export function openChat(chat: Chat): void {
  window.dispatchEvent(new CustomEvent<OpenChatDetail>(OPEN_CHAT_EVENT, { detail: { chat } }));
}

/** Open a chat known only by its id (e.g. from a topic's side-chat list). */
export async function openChatById(id: number): Promise<void> {
  openChat(await api.chats.get(id));
}

// A reply to scroll to once its topic's transcript is on screen. Kept outside
// React: the topic panel that consumes it mounts after the section switch.
let pendingJump: { topicId: number; messageId: number } | null = null;

/** Switch to Topics, open `topicId` and, when given, scroll to `messageId`. */
export function openTopic(topicId: number, messageId?: number | null): void {
  pendingJump = messageId != null ? { topicId, messageId } : null;
  window.dispatchEvent(
    new CustomEvent<OpenTopicDetail>(OPEN_TOPIC_EVENT, { detail: { topicId } }),
  );
}

/** The reply waiting to be scrolled to in `topicId`, if any (consumed). */
export function takePendingJump(topicId: number): number | null {
  if (pendingJump?.topicId !== topicId) return null;
  const { messageId } = pendingJump;
  pendingJump = null;
  return messageId;
}

/** Peek without consuming: the panel waits for the turn index before jumping. */
export function hasPendingJump(topicId: number): boolean {
  return pendingJump?.topicId === topicId;
}

/** Open a chat from an in-app `/chats/<slug>` link without reloading. */
export async function openChatBySlug(slug: string): Promise<void> {
  openChat(await api.chats.getBySlug(slug));
}

/**
 * Start a side chat from `topicId` and open it. `messageId` starts it from one
 * of the topic's replies; `quote` narrows that reply to an excerpt.
 */
export async function startSideChat(
  topicId: number,
  messageId?: number | null,
  quote?: string | null,
): Promise<Chat> {
  const chat = await api.sideChats.create(topicId, messageId, quote);
  openChat(chat);
  return chat;
}

type Confirm = (options: {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
}) => Promise<boolean>;

/**
 * Archive a topic, first asking whether its open side chats go with it. Only
 * asks when it has some. Declining still archives the topic.
 */
export async function archiveTopic(topicId: number, confirm: Confirm): Promise<void> {
  let count = 0;
  try {
    count = (await api.sideChats.list(topicId)).length;
  } catch {
    // can't tell: archive the topic alone
  }
  const sideChatsToo =
    count > 0 &&
    (await confirm({
      title: "Archive its side chats too?",
      message:
        count === 1
          ? "This topic has 1 open side chat. Archive it with the topic? Restoring the topic brings it back."
          : `This topic has ${count} open side chats. Archive them with the topic? Restoring the topic brings them back.`,
      confirmLabel: "Archive side chats too",
      cancelLabel: "Keep them open",
    }));
  await api.topics.archive(topicId, { sideChatsToo });
}

// "Send to topic" is offered both in the chat's header and as `/send-to-topic`;
// the header doesn't own the chat panel, so it asks through this channel (like
// lib/summaryOpen.ts does for the topic summary).
const TOPIC_NOTE_EVENT = "precursor:side-chat-topic-note";

export function requestTopicNote(chatId: number): void {
  window.dispatchEvent(new CustomEvent<number>(TOPIC_NOTE_EVENT, { detail: chatId }));
}

export function subscribeTopicNote(chatId: number, fn: () => void): () => void {
  const handler = (e: Event) => {
    if ((e as CustomEvent<number>).detail === chatId) fn();
  };
  window.addEventListener(TOPIC_NOTE_EVENT, handler);
  return () => window.removeEventListener(TOPIC_NOTE_EVENT, handler);
}
