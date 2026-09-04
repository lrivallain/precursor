/**
 * Cross-window event bus client.
 *
 * Subscribes to the server's /api/events SSE stream and dispatches typed
 * events to React listeners. Events that originated from this window
 * (matched on the per-window CLIENT_ID) are filtered out so we don't fight
 * with locally optimistic UI updates.
 */

import { CLIENT_ID } from "./clientId";
import { mcpAuthStore } from "./mcpAuth";
import { authLog } from "./workiqAuthLog";
import { emitWorkiqAuthUrl } from "./workiqSignIn";

export type BusEvent =
  | { type: "topic.changed"; topic_id: number | null; chat_id?: number | null }
  | { type: "chat.changed"; chat_id?: number | null }
  | { type: "message.changed"; topic_id?: number | null; chat_id?: number | null }
  | { type: "stream.started"; topic_id?: number | null; chat_id?: number | null }
  | { type: "stream.ended"; topic_id?: number | null; chat_id?: number | null }
  | { type: "reminder.changed"; topic_id?: number | null; chat_id?: number | null }
  | {
      type: "read.changed";
      topic_id?: number | null;
      chat_id?: number | null;
      agent_session_id?: number | null;
    }
  | {
      type: "agent.changed";
      agent_session_id?: number | null;
      topic_id?: number | null;
      chat_id?: number | null;
    }
  | { type: "meeting.changed"; meeting_session_id?: number | null }
  | {
      type: "workflow.changed";
      workflow_id?: number | null;
      /** Run state at the moment of the change, so the client can notify on it. */
      workflow_status?: string | null;
      workflow_name?: string | null;
    };

type Handler = (event: BusEvent) => void;

const handlers = new Set<Handler>();
let source: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let started = false;

function dispatch(type: BusEvent["type"], raw: string): void {
  let payload: {
    client_id?: string;
    topic_id?: number | null;
    chat_id?: number | null;
    agent_session_id?: number | null;
    meeting_session_id?: number | null;
    workflow_id?: number | null;
    status?: string | null;
    name?: string | null;
  };
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
    agent_session_id: payload.agent_session_id ?? null,
    meeting_session_id: payload.meeting_session_id ?? null,
    workflow_id: payload.workflow_id ?? null,
    workflow_status: payload.status ?? null,
    workflow_name: payload.name ?? null,
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
  source.addEventListener("chat.changed", (e) =>
    dispatch("chat.changed", (e as MessageEvent).data),
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
  source.addEventListener("read.changed", (e) =>
    dispatch("read.changed", (e as MessageEvent).data),
  );
  source.addEventListener("agent.changed", (e) =>
    dispatch("agent.changed", (e as MessageEvent).data),
  );
  source.addEventListener("meeting.changed", (e) =>
    dispatch("meeting.changed", (e as MessageEvent).data),
  );
  source.addEventListener("workflow.changed", (e) =>
    dispatch("workflow.changed", (e as MessageEvent).data),
  );
  // A background run (e.g. a scheduled /guard probe) found an MCP server parked
  // in needs_auth. Drive the app-global re-authenticate banner directly — this
  // notice carries server/message fields the typed BusEvent bus doesn't relay.
  source.addEventListener("mcp.auth_required", (e) => {
    try {
      const payload = JSON.parse((e as MessageEvent).data) as {
        server?: string;
        message?: string;
      };
      const server = payload.server ?? "workiq";
      authLog(server, "SSE mcp.auth_required", { message: payload.message });
      mcpAuthStore.report(server, payload.message ?? "Sign-in required.");
    } catch {
      // Ignore malformed payloads.
    }
  });
  // The interactive WorkIQ sign-in surfaces its OAuth authorization URL here so
  // the window that started it can steer its script-opened popup to it. Not
  // client-id filtered: the requesting window is the one that needs it.
  source.addEventListener("mcp.auth_url", (e) => {
    try {
      const payload = JSON.parse((e as MessageEvent).data) as {
        url?: string;
        server?: string;
      };
      if (payload.url) emitWorkiqAuthUrl(payload.url, payload.server ?? "workiq");
    } catch {
      // Ignore malformed payloads.
    }
  });
  // A sign-in completed in some window — drop a stale banner here so windows
  // that only ever saw the needs-auth notice stop prompting for credentials that
  // are now fresh. Not client-id filtered: the originating window already
  // cleared locally, the rest are precisely who this is for.
  source.addEventListener("mcp.auth_resolved", (e) => {
    try {
      const payload = JSON.parse((e as MessageEvent).data) as { server?: string };
      const server = payload.server ?? "workiq";
      authLog(server, "SSE mcp.auth_resolved");
      mcpAuthStore.resolve(server);
    } catch {
      // Ignore malformed payloads.
    }
  });
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

/**
 * Minimum gap between two runs of a burst-coalesced refresh.
 *
 * Short enough that a live view still reads as real-time, long enough that a
 * token-cadence event stream collapses into a handful of refreshes per second
 * instead of one per event.
 */
export const REFRESH_COALESCE_MS = 400;

/** A coalesced refresh trigger: call it as often as you like. */
export type CoalescedRunner = {
  (): void;
  /** Drop any pending trailing run (call from effect cleanup). */
  cancel(): void;
};

/**
 * Wrap a refetch so a burst of events costs at most one request per window.
 *
 * An agent turn emits SDK events at token cadence, and every `agent.changed`
 * listener answers with a full refetch (the roster, the run rail, the timeline,
 * the artifact list…). Uncoalesced, one turn fans a few hundred signals into
 * thousands of overlapping requests: the browser starts rejecting them with
 * `ERR_INSUFFICIENT_RESOURCES`, memory climbs with responses nobody reads, and
 * the UI flickers as each late response overwrites the last.
 *
 * Runs on the leading edge so the first signal lands immediately, then admits at
 * most one run per `windowMs` and never overlaps an in-flight one — a burst
 * always ends with exactly one trailing run, so the final state is never stale.
 */
export function coalesce(
  handler: () => void | Promise<void>,
  windowMs: number = REFRESH_COALESCE_MS,
): CoalescedRunner {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let inFlight = false;
  let queued = false;
  let lastRun = 0;
  let cancelled = false;

  const run = (): void => {
    if (cancelled) return;
    queued = false;
    inFlight = true;
    lastRun = Date.now();
    void Promise.resolve()
      .then(handler)
      .catch(() => {
        // Callers own their own error handling; a rejection must not wedge the
        // runner in the in-flight state.
      })
      .then(() => {
        inFlight = false;
        // Measure the window from completion, so a slow endpoint backs itself
        // off instead of queueing another request the moment it answers.
        lastRun = Date.now();
        if (queued) schedule();
      });
  };

  const schedule = (): void => {
    if (cancelled || inFlight || timer != null) return;
    const wait = Math.max(0, windowMs - (Date.now() - lastRun));
    if (wait === 0) {
      run();
      return;
    }
    timer = setTimeout(() => {
      timer = null;
      run();
    }, wait);
  };

  const trigger = (() => {
    queued = true;
    schedule();
  }) as CoalescedRunner;

  trigger.cancel = () => {
    cancelled = true;
    queued = false;
    if (timer != null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return trigger;
}

/**
 * Subscribe to `agent.changed` with burst coalescing (see {@link coalesce}).
 *
 * `agentId` narrows to one session; a broadcast (no `agent_session_id`) always
 * matches, mirroring how the raw handlers treated an unaddressed signal as
 * "something moved, refresh". Pass `null` to react to every agent.
 */
export function subscribeAgentChanged(
  agentId: number | null,
  handler: () => void | Promise<void>,
  windowMs: number = REFRESH_COALESCE_MS,
): () => void {
  const trigger = coalesce(handler, windowMs);
  const off = eventBus.subscribe((ev) => {
    if (ev.type !== "agent.changed") return;
    if (agentId != null && ev.agent_session_id != null && ev.agent_session_id !== agentId) return;
    trigger();
  });
  return () => {
    off();
    trigger.cancel();
  };
}
