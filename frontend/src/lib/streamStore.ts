import { useSyncExternalStore } from "react";
import { streamChat } from "./sse";
import type { Attachment, Message } from "./types";

export interface UsageReport {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

interface Session {
  topicId: number;
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
  private sessions = new Map<number, Session>();
  private remoteStreaming = new Set<number>();
  private version = 0;
  private listeners = new Set<Listener>();
  private onCompleteCb: ((topicId: number) => void) | null = null;

  setOnComplete(cb: ((topicId: number) => void) | null): void {
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

  hasSession(topicId: number): boolean {
    return this.sessions.has(topicId);
  }

  isStreaming(topicId: number): boolean {
    return (
      (this.sessions.get(topicId)?.streaming ?? false) ||
      this.remoteStreaming.has(topicId)
    );
  }

  pendingContent(topicId: number): string {
    return this.sessions.get(topicId)?.pendingContent ?? "";
  }

  bufferedMessages(topicId: number): Message[] {
    return this.sessions.get(topicId)?.messages ?? [];
  }

  lastUsage(topicId: number): UsageReport | null {
    return this.sessions.get(topicId)?.lastUsage ?? null;
  }

  streamingTopicIds(): number[] {
    const out = new Set<number>();
    for (const s of this.sessions.values()) {
      if (s.streaming) out.add(s.topicId);
    }
    for (const id of this.remoteStreaming) out.add(id);
    return [...out];
  }

  setRemoteStreaming(topicId: number, value: boolean): void {
    const has = this.remoteStreaming.has(topicId);
    if (value && !has) {
      this.remoteStreaming.add(topicId);
      this.notify();
    } else if (!value && has) {
      this.remoteStreaming.delete(topicId);
      this.notify();
    }
  }

  clear(topicId: number): void {
    if (this.sessions.delete(topicId)) this.notify();
  }

  stop(topicId: number): void {
    this.sessions.get(topicId)?.abort.abort();
  }

  async start(
    topicId: number,
    content: string,
    promptOverride?: string,
    attachments?: Attachment[],
  ): Promise<void> {
    if (this.sessions.get(topicId)?.streaming) return;
    const controller = new AbortController();
    const optimisticId = -Date.now();
    const session: Session = {
      topicId,
      streaming: true,
      pendingContent: "",
      messages: [
        {
          id: optimisticId,
          topic_id: topicId,
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
    this.sessions.set(topicId, session);
    this.notify();

    try {
      await streamChat(
        topicId,
        {
          content,
          ...(promptOverride ? { prompt_override: promptOverride } : {}),
          ...(attachments && attachments.length
            ? { attachment_ids: attachments.map((a) => a.id) }
            : {}),
        },
        {
          signal: controller.signal,
          onEvent: (ev) => this.handleEvent(topicId, ev),
        },
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        console.error(err);
      }
    } finally {
      const cur = this.sessions.get(topicId);
      if (cur) {
        cur.streaming = false;
        this.notify();
      }
      this.onCompleteCb?.(topicId);
    }
  }

  private handleEvent(
    topicId: number,
    ev: { event: string; data: string },
  ): void {
    const session = this.sessions.get(topicId);
    if (!session) return;
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
          topic_id: topicId,
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
        topic_id: topicId,
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
          topic_id: topicId,
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
        topic_id: topicId,
        role: "system",
        content: payload.message as string,
        tool_calls: null,
        created_at: now,
      });
    } else if (ev.event === "done") {
      const usage = session.pendingUsage;
      session.pendingUsage = null;
      session.messages.push({
        id: payload.id as number,
        topic_id: topicId,
        role: "assistant",
        content: payload.content as string,
        tool_calls: null,
        prompt_tokens: usage?.prompt_tokens ?? null,
        completion_tokens: usage?.completion_tokens ?? null,
        created_at: now,
      });
      session.pendingContent = "";
    } else if (ev.event === "error") {
      session.messages.push({
        id: -Date.now(),
        topic_id: topicId,
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
