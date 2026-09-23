/**
 * Minimal Server-Sent Events client that supports POST bodies.
 *
 * Browsers' built-in EventSource only allows GET; the chat endpoint takes a
 * JSON body, so we stream the response manually.
 */

import { CLIENT_ID } from "./clientId";

export interface SSEEvent {
  event: string;
  data: string;
}

export interface StreamChatOptions {
  signal?: AbortSignal;
  onEvent: (event: SSEEvent) => void;
}

/**
 * POST a JSON body to an SSE endpoint and dispatch each event to `onEvent`.
 * Every streaming call shares this prologue; they differ only in URL and body.
 */
export async function postSSE(
  url: string,
  body: unknown,
  { signal, onEvent }: StreamChatOptions,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "X-Client-Id": CLIENT_ID,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Stream failed: ${res.status} ${res.statusText}`);
  }

  await consumeStream(res.body, onEvent);
}

/** Request body shared by the topic and flat-chat stream endpoints. */
export interface ChatStreamBody {
  content: string;
  model?: string;
  prompt_override?: string;
  attachment_ids?: number[];
  note_attachment_ids?: number[];
  retry_message_id?: number;
}

export function streamChat(
  topicId: number,
  body: ChatStreamBody,
  opts: StreamChatOptions,
): Promise<void> {
  return postSSE(`/api/topics/${topicId}/messages/stream`, body, opts);
}

/**
 * POST to a Workspace's ephemeral chat endpoint and stream the reply.
 * History is supplied by the caller (workspace chat is not server-persisted).
 */
export function streamWorkspaceChat(
  workspaceId: number,
  body: {
    content: string;
    history: { role: "user" | "assistant"; content: string }[];
    path?: string | null;
    model?: string;
    prompt_override?: string;
  },
  opts: StreamChatOptions,
): Promise<void> {
  return postSSE(`/api/workspaces/${workspaceId}/chat/stream`, body, opts);
}

/**
 * POST to a flat Chat session's stream endpoint and stream the reply.
 * Chats persist server-side like topics, but have no GitHub context.
 */
export function streamChatSession(
  chatId: number,
  body: ChatStreamBody,
  opts: StreamChatOptions,
): Promise<void> {
  return postSSE(`/api/chats/${chatId}/messages/stream`, body, opts);
}

/**
 * POST a question to a live meeting session and stream the answer.
 * The exchange is not persisted server-side; the caller renders it live.
 */
export function streamMeetingAsk(
  sessionId: number,
  question: string,
  opts: StreamChatOptions,
): Promise<void> {
  return postSSE(`/api/live/${sessionId}/ask`, { question }, opts);
}

async function consumeStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: SSEEvent) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // Per SSE spec: lines end with CR, LF, or CRLF; events are separated by a
  // blank line. Normalise CRLF -> LF so a single split handles both.
  const flush = (final: boolean): void => {
    while (true) {
      const sep = buffer.indexOf("\n\n");
      if (sep === -1) {
        if (final && buffer.length > 0) {
          parseFrame(buffer, onEvent);
          buffer = "";
        }
        return;
      }
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      parseFrame(frame, onEvent);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    flush(false);
  }
  buffer += decoder.decode();
  flush(true);
}

function parseFrame(frame: string, onEvent: (e: SSEEvent) => void): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trimStart();
    } else if (line.startsWith("data:")) {
      // SSE spec: strip exactly one leading space if present, preserve the rest.
      const v = line.slice(5);
      dataLines.push(v.startsWith(" ") ? v.slice(1) : v);
    }
  }
  if (dataLines.length > 0) onEvent({ event, data: dataLines.join("\n") });
}
