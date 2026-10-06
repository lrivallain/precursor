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

/** Start a side chat from `topicId` (or one of its replies) and open it. */
export async function startSideChat(topicId: number, messageId?: number | null): Promise<Chat> {
  const chat = await api.sideChats.create(topicId, messageId);
  openChat(chat);
  return chat;
}
