/**
 * Minimal Server-Sent Events client that supports POST bodies.
 *
 * Browsers' built-in EventSource only allows GET; the chat endpoint takes a
 * JSON body, so we stream the response manually.
 */

export interface SSEEvent {
  event: string;
  data: string;
}

export interface StreamChatOptions {
  signal?: AbortSignal;
  onEvent: (event: SSEEvent) => void;
}

export async function streamChat(
  topicId: number,
  body: { content: string; model?: string },
  { signal, onEvent }: StreamChatOptions,
): Promise<void> {
  const res = await fetch(`/api/topics/${topicId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Stream failed: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    // Frames are separated by a blank line per SSE spec.
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length > 0) onEvent({ event, data: dataLines.join("\n") });
    }
  }
}
