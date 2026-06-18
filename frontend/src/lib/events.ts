/**
 * Cross-window event bus client.
 *
 * Subscribes to the server's /api/events SSE stream and dispatches typed
 * events to React listeners. Events that originated from this window
 * (matched on the per-window CLIENT_ID) are filtered out so we don't fight
 * with locally optimistic UI updates.
 */

import { CLIENT_ID } from "./clientId";

export type BusEvent =
  | { type: "topic.changed"; topic_id: number | null; chat_id?: number | null }
  | { type: "message.changed"; topic_id?: number | null; chat_id?: number | null }
  | { type: "stream.started"; topic_id?: number | null; chat_id?: number | null }
  | { type: "stream.ended"; topic_id?: number | null; chat_id?: number | null }
  | { type: "reminder.changed"; topic_id?: number | null; chat_id?: number | null };

type Handler = (event: BusEvent) => void;

const handlers = new Set<Handler>();
let source: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let started = false;

function dispatch(type: BusEvent["type"], raw: string): void {
  let payload: { client_id?: string; topic_id?: number | null; chat_id?: number | null };
  try {
    payload = JSON.parse(raw);
  } catch {
    return;
  }
  if (payload.client_id && payload.client_id === CLIENT_ID) return;
  const event = {
    type,
    topic_id: payload.topic_id ?? null,
    chat_id: payload.chat_id ?? null,
  } as BusEvent;
  for (const h of handlers) {
    try {
      h(event);
    } catch (err) {
      console.warn("event handler threw", err);
    }
  }
}

function connect(): void {
  if (source) return;
  source = new EventSource("/api/events");
  source.addEventListener("topic.changed", (e) =>
    dispatch("topic.changed", (e as MessageEvent).data),
  );
  source.addEventListener("message.changed", (e) =>
    dispatch("message.changed", (e as MessageEvent).data),
  );
  source.addEventListener("stream.started", (e) =>
    dispatch("stream.started", (e as MessageEvent).data),
  );
  source.addEventListener("stream.ended", (e) =>
    dispatch("stream.ended", (e as MessageEvent).data),
  );
  source.addEventListener("reminder.changed", (e) =>
    dispatch("reminder.changed", (e as MessageEvent).data),
  );
  source.onerror = () => {
    source?.close();
    source = null;
    // EventSource auto-retries, but its built-in retry policy is opaque.
    // We control it explicitly so reconnect feels immediate after the
    // backend restarts in dev.
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1500);
  };
}

export const eventBus = {
  start(): void {
    if (started) return;
    started = true;
    connect();
  },
  subscribe(handler: Handler): () => void {
    handlers.add(handler);
    return () => {
      handlers.delete(handler);
    };
  },
};
