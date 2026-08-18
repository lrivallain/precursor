/**
 * System-role messages are the transcript's out-of-band notices: local command
 * acknowledgements ("Saved memory #3") *and* failures (a provider rejection, a
 * tool-round cap). Only the latter should read as a failure, so the two need to
 * be told apart at render time.
 *
 * Backend stream failures persist as `Error: <message>` (see
 * `turn_engine._persist_system_message`), which is the canonical marker for a
 * turn that did not produce an answer. Client-side notices carry an explicit
 * `is_error` flag instead, since they are never persisted.
 */

import type { Message } from "./types";

export const SYSTEM_ERROR_PREFIX = "Error: ";

/** True when a system-role message reports a failure rather than an outcome. */
export function isErrorNotice(message: Pick<Message, "content" | "is_error">): boolean {
  return message.is_error === true || message.content.startsWith(SYSTEM_ERROR_PREFIX);
}

/** The notice text without its `Error: ` marker (the badge carries that). */
export function errorNoticeBody(content: string): string {
  return content.startsWith(SYSTEM_ERROR_PREFIX)
    ? content.slice(SYSTEM_ERROR_PREFIX.length).trimStart()
    : content;
}

/**
 * The user turn a failed conversation tail belongs to, or null when the
 * transcript doesn't end on a failure.
 *
 * A turn "failed" when the last message is an error notice: everything the
 * backend wrote after that user turn (a partial answer, tool rows, the error
 * itself) is replaced when it is replayed, so we walk back to the user message
 * that started it. Synthetic (negative-id) rows can't be retried server-side.
 */
export function failedTurnUserMessageId(messages: Message[]): number | null {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "system" || !isErrorNotice(last)) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== "user") continue;
    return messages[i].id > 0 ? messages[i].id : null;
  }
  return null;
}
