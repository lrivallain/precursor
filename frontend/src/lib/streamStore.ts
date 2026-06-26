import { useSyncExternalStore } from "react";
import { mcpAuthStore } from "./mcpAuth";
import { streamChat, streamChatSession } from "./sse";
import type { Attachment, Message } from "./types";

export interface UsageReport {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

// A conversation is either a topic or a flat chat session. The store keys
// everything by a `kind:id` string so topic 5 and chat 5 never collide.
export type ConvKind = "topic" | "chat";

export function convKey(kind: ConvKind, id: number): string {
  return `${kind}:${id}`;
}

function parseKey(key: string): { kind: ConvKind; id: number } {
  const idx = key.indexOf(":");
  return { kind: key.slice(0, idx) as ConvKind, id: Number(key.slice(idx + 1)) };
}

// FK fields to stamp on buffered messages so they round-trip like persisted ones.
function containerFields(
  kind: ConvKind,
  id: number,
): { topic_id: number | null; chat_id: number | null } {
  return kind === "topic" ? { topic_id: id, chat_id: null } : { topic_id: null, chat_id: id };
}

interface Session {
  key: string;
  kind: ConvKind;
  id: number;
  streaming: boolean;
  pendingContent: string;
  messages: Message[];
  abort: AbortController;
  // Usage reported for the in-progress round, applied to the next assistant
  // message persisted by `tool_calls` or `done`.
  pendingUsage: UsageReport | null;
  // Usage of the most recent completed round.
  lastUsage: UsageReport | null;
}

type Listener = () => void;

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}

class StreamStore {
  private sessions = new Map<string, Session>();
  private remoteStreaming = new Set<string>();
  private version = 0;
  private listeners = new Set<Listener>();
  private onCompleteCb: ((key: string) => void) | null = null;

  setOnComplete(cb: ((key: string) => void) | null): void {
    this.onCompleteCb = cb;
  }

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  getSnapshot = (): number => this.version;

  private notify(): void {
    this.version++;
    for (const l of this.listeners) l();
  }

  hasSession(key: string): boolean {
    return this.sessions.has(key);
  }

  isStreaming(key: string): boolean {
    return (
      (this.sessions.get(key)?.streaming ?? false) || this.remoteStreaming.has(key)
    );
  }

  pendingContent(key: string): string {
    return this.sessions.get(key)?.pendingContent ?? "";
  }

  bufferedMessages(key: string): Message[] {
    return this.sessions.get(key)?.messages ?? [];
  }

  lastUsage(key: string): UsageReport | null {
    return this.sessions.get(key)?.lastUsage ?? null;
  }

  /** Streaming conversation ids of a given kind (local + remote). */
  streamingIds(kind: ConvKind): number[] {
    const out = new Set<number>();
    for (const s of this.sessions.values()) {
      if (s.streaming && s.kind === kind) out.add(s.id);
    }
    for (const key of this.remoteStreaming) {
      const { kind: k, id } = parseKey(key);
      if (k === kind) out.add(id);
    }
    return [...out];
  }

  setRemoteStreaming(key: string, value: boolean): void {
    const has = this.remoteStreaming.has(key);
    if (value && !has) {
      this.remoteStreaming.add(key);
      this.notify();
    } else if (!value && has) {
      this.remoteStreaming.delete(key);
      this.notify();
    }
  }

  clear(key: string): void {
    if (this.sessions.delete(key)) this.notify();
  }

  stop(key: string): void {
    this.sessions.get(key)?.abort.abort();
  }

  async start(
    key: string,
    content: string,
    promptOverride?: string,
    attachments?: Attachment[],
    noteAttachmentIds?: number[],
  ): Promise<void> {
    if (this.sessions.get(key)?.streaming) return;
    const { kind, id } = parseKey(key);
    const controller = new AbortController();
    const optimisticId = -Date.now();
    const session: Session = {
      key,
      kind,
      id,
      streaming: true,
      pendingContent: "",
      messages: [
        {
          id: optimisticId,
          ...containerFields(kind, id),
          role: "user",
          content,
          tool_calls: null,
          created_at: new Date().toISOString(),
          attachments: attachments && attachments.length ? attachments : [],
        },
      ],
      abort: controller,
      pendingUsage: null,
      lastUsage: null,
    };
    this.sessions.set(key, session);
    this.notify();

    try {
      const onEvent = (ev: { event: string; data: string }): void =>
        this.handleEvent(key, ev);
      if (kind === "topic") {
        await streamChat(
          id,
          {
            content,
            ...(promptOverride ? { prompt_override: promptOverride } : {}),
            ...(attachments && attachments.length
              ? { attachment_ids: attachments.map((a) => a.id) }
              : {}),
            ...(noteAttachmentIds && noteAttachmentIds.length
              ? { note_attachment_ids: noteAttachmentIds }
              : {}),
          },
          { signal: controller.signal, onEvent },
        );
      } else {
        await streamChatSession(
          id,
          {
            content,
            ...(promptOverride ? { prompt_override: promptOverride } : {}),
            ...(attachments && attachments.length
              ? { attachment_ids: attachments.map((a) => a.id) }
              : {}),
            ...(noteAttachmentIds && noteAttachmentIds.length
              ? { note_attachment_ids: noteAttachmentIds }
              : {}),
          },
          { signal: controller.signal, onEvent },
        );
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        console.error(err);
      }
    } finally {
      const cur = this.sessions.get(key);
      if (cur) {
        cur.streaming = false;
        this.notify();
      }
      this.onCompleteCb?.(key);
    }
  }

  private handleEvent(key: string, ev: { event: string; data: string }): void {
    const session = this.sessions.get(key);
    if (!session) return;
    const fk = containerFields(session.kind, session.id);
    const payload = JSON.parse(ev.data) as Record<string, unknown>;
    const now = new Date().toISOString();

    if (ev.event === "user_message") {
      // Reconcile the optimistic user message inserted by start() — keep the
      // displayed content stable, just upgrade the id to the persisted one.
      const realId = payload.id as number;
      const realContent = payload.content as string;
      const realAttachments =
        (payload.attachments as Attachment[] | undefined) ?? [];
      const idx = session.messages.findIndex(
        (m) => m.role === "user" && m.id < 0,
      );
      if (idx >= 0) {
        session.messages[idx] = {
          ...session.messages[idx],
          id: realId,
          content: realContent,
          attachments: realAttachments,
        };
      } else {
        session.messages.push({
          id: realId,
          ...fk,
          role: "user",
          content: realContent,
          tool_calls: null,
          created_at: now,
          attachments: realAttachments,
        });
      }
    } else if (ev.event === "delta") {
      session.pendingContent += payload.content as string;
    } else if (ev.event === "usage") {
      const usage: UsageReport = {
        prompt_tokens: (payload.prompt_tokens as number) ?? 0,
        completion_tokens: (payload.completion_tokens as number) ?? 0,
        total_tokens: (payload.total_tokens as number) ?? 0,
      };
      session.pendingUsage = usage;
      session.lastUsage = usage;
      // If the assistant message it refers to is already in the buffer
      // (e.g. legacy ordering), patch it in place.
      const messageId = payload.message_id as number | undefined;
      if (typeof messageId === "number") {
        const idx = session.messages.findIndex((m) => m.id === messageId);
        if (idx >= 0) {
          session.messages[idx] = {
            ...session.messages[idx],
            prompt_tokens: usage.prompt_tokens,
            completion_tokens: usage.completion_tokens,
          };
        }
      }
    } else if (ev.event === "tool_calls") {
      const assistantId = payload.assistant_id as number;
      const calls = payload.calls as Array<{
        id: string;
        name: string;
        arguments: string;
      }>;
      const usage = session.pendingUsage;
      session.pendingUsage = null;
      session.messages.push({
        id: assistantId,
        ...fk,
        role: "assistant",
        content: session.pendingContent,
        tool_calls: JSON.stringify(
          calls.map((c) => ({
            id: c.id,
            type: "function",
            function: { name: c.name, arguments: c.arguments },
          })),
        ),
        prompt_tokens: usage?.prompt_tokens ?? null,
        completion_tokens: usage?.completion_tokens ?? null,
        created_at: now,
      });
      for (const c of calls) {
        session.messages.push({
          id: -Math.abs(hashString(c.id)),
          ...fk,
          role: "tool",
          content: "",
          tool_calls: JSON.stringify({
            tool_call_id: c.id,
            name: c.name,
            arguments: c.arguments,
            is_error: false,
            pending: true,
          }),
          created_at: now,
        });
      }
      session.pendingContent = "";
    } else if (ev.event === "tool_result") {
      const tcId = payload.tool_call_id as string;
      const messageId = payload.message_id as number;
      session.messages = session.messages.map((m) => {
        if (m.role !== "tool" || !m.tool_calls) return m;
        let meta: Record<string, unknown>;
        try {
          meta = JSON.parse(m.tool_calls);
        } catch {
          return m;
        }
        if (meta.tool_call_id !== tcId) return m;
        return {
          ...m,
          id: messageId,
          content: payload.content as string,
          tool_calls: JSON.stringify({
            ...meta,
            is_error: Boolean(payload.is_error),
            pending: false,
          }),
        };
      });
    } else if (ev.event === "system") {
      session.messages.push({
        id: -Date.now(),
        ...fk,
        role: "system",
        content: payload.message as string,
        tool_calls: null,
        created_at: now,
      });
    } else if (ev.event === "mcp_auth_required") {
      // A background MCP connect needs an interactive sign-in. Surface it via
      // the app-global banner rather than as an inert system message.
      mcpAuthStore.report(
        (payload.server as string) ?? "workiq",
        (payload.message as string) ?? "Sign-in required.",
      );
    } else if (ev.event === "done") {
      const usage = session.pendingUsage;
      session.pendingUsage = null;
      session.messages.push({
        id: payload.id as number,
        ...fk,
        role: "assistant",
        content: payload.content as string,
        tool_calls: null,
        prompt_tokens: usage?.prompt_tokens ?? null,
        completion_tokens: usage?.completion_tokens ?? null,
        model: (payload.model as string | null) ?? null,
        elapsed_ms: (payload.elapsed_ms as number | null) ?? null,
        created_at: now,
      });
      session.pendingContent = "";
    } else if (ev.event === "suggestions") {
      // Emitted right after `done`; patch the just-created assistant turn with
      // the follow-up chips (matched by the persisted message id).
      const messageId = payload.message_id as number | undefined;
      const items = (payload.items as string[] | undefined) ?? [];
      if (typeof messageId === "number") {
        const idx = session.messages.findIndex((m) => m.id === messageId);
        if (idx >= 0) {
          session.messages[idx] = { ...session.messages[idx], suggestions: items };
        }
      }
    } else if (ev.event === "error") {
      session.messages.push({
        id: -Date.now(),
        ...fk,
        role: "system",
        content: `Error: ${payload.message as string}`,
        tool_calls: null,
        created_at: now,
      });
    }
    // Always publish a fresh array reference so React memos that depend on
    // `bufferedMessages(...)` recompute. Several branches above mutate the
    // list in place (push / index assignment); without this, the final
    // assistant turn after a tool round never appears until the topic is
    // reloaded.
    session.messages = [...session.messages];
    this.notify();
  }
}

export const streamStore = new StreamStore();

export function useStreamVersion(): number {
  return useSyncExternalStore(
    streamStore.subscribe,
    streamStore.getSnapshot,
    streamStore.getSnapshot,
  );
}
