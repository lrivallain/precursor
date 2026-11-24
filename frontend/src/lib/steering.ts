import { call } from "./apiClient";

/**
 * A message the user sent while the conversation's turn was running. It waits
 * in the server's queue until the turn's next model round takes it; the
 * `user_message` event that echoes it back carries its `steerId`.
 */
export interface QueuedSteer {
  content: string;
  /** Set once the server queued it; null while the request is in flight. */
  steerId: string | null;
}

/** Queue `content` for the turn running in a topic or chat. */
export function postSteer(kind: "topic" | "chat", id: number, content: string) {
  return kind === "topic"
    ? call("POST /api/topics/{topic_id}/messages/steer", {
        path: { topic_id: id },
        body: { content },
      })
    : call("POST /api/chats/{chat_id}/messages/steer", {
        path: { chat_id: id },
        body: { content },
      });
}

type SteerResponse = { queued: boolean; steer_id?: string | null };

/**
 * Sends a turn's queued messages to the server one at a time, in the order they
 * were written, and only once the turn has started there (before that the
 * server has no queue and would refuse them). After a refusal, the turn has
 * ended: later messages are not sent either, so all of them go out together,
 * still in order, as the next turn.
 */
export class SteerSender {
  private chain: Promise<void> = Promise.resolve();
  private stopped = false;
  private markStarted!: () => void;
  private readonly started = new Promise<void>((resolve) => (this.markStarted = resolve));

  constructor(private readonly post: (content: string) => Promise<SteerResponse>) {}

  /** The turn's first event arrived: the server takes messages from now on. */
  start(): void {
    this.markStarted();
  }

  send(item: QueuedSteer): void {
    this.chain = this.chain.then(async () => {
      await this.started;
      if (this.stopped) return;
      try {
        const res = await this.post(item.content);
        if (res.queued) item.steerId = res.steer_id ?? null;
        else this.stopped = true;
      } catch {
        this.stopped = true;
      }
    });
  }

  /** The turn's request has ended: send nothing more, and resolve once every answer is in. */
  settled(): Promise<void> {
    this.stopped = true;
    this.markStarted();
    return this.chain;
  }
}

/**
 * Drop the queued message a `user_message` echo answers: by its id, or, when the
 * echo beat the steer request's response, the oldest still-unconfirmed one with
 * the same text.
 */
export function withoutEchoed(
  queued: QueuedSteer[],
  steerId: string,
  content: string,
): QueuedSteer[] {
  let idx = queued.findIndex((q) => q.steerId === steerId);
  if (idx < 0) idx = queued.findIndex((q) => q.steerId === null && q.content === content);
  return idx < 0 ? queued : [...queued.slice(0, idx), ...queued.slice(idx + 1)];
}

/** The messages the turn never took, as one prompt. */
export function joinUnsent(queued: QueuedSteer[]): string {
  return queued.map((q) => q.content.trim()).filter(Boolean).join("\n\n");
}
