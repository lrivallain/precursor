import { describe, expect, it } from "vitest";
import { joinUnsent, SteerSender, withoutEchoed, type QueuedSteer } from "./steering";

const queued: QueuedSteer[] = [
  { content: "shorter", steerId: "a" },
  { content: "in French", steerId: null },
  { content: "shorter", steerId: null },
];

describe("withoutEchoed", () => {
  it("drops the message the echo names", () => {
    expect(withoutEchoed(queued, "a", "shorter")).toEqual(queued.slice(1));
  });

  it("falls back to the oldest unconfirmed message with the same text", () => {
    expect(withoutEchoed(queued, "zz", "shorter")).toEqual(queued.slice(0, 2));
  });

  it("leaves the queue alone when nothing matches", () => {
    expect(withoutEchoed(queued, "zz", "other")).toBe(queued);
  });
});

describe("joinUnsent", () => {
  it("joins what the turn never took into one prompt", () => {
    expect(joinUnsent(queued)).toBe("shorter\n\nin French\n\nshorter");
    expect(joinUnsent([{ content: "  ", steerId: null }])).toBe("");
  });
});

describe("SteerSender", () => {
  // Let queued sends run, as they would while the turn streams.
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
  const item = (content: string): QueuedSteer => ({ content, steerId: null });

  it("waits for the turn to start, then sends one message at a time, in order", async () => {
    const sent: string[] = [];
    const sender = new SteerSender(async (turnId, content) => {
      sent.push(`${turnId}:${content}`);
      return { queued: true, steer_id: `id-${content}` };
    });
    const a = item("a");
    const b = item("b");
    sender.send(a);
    sender.send(b);
    await Promise.resolve();
    expect(sent).toEqual([]);
    sender.start("t1");
    await flush();
    await sender.settled();
    expect(sent).toEqual(["t1:a", "t1:b"]);
    expect([a.steerId, b.steerId]).toEqual(["id-a", "id-b"]);
  });

  it("holds every later message back once one is refused", async () => {
    const sent: string[] = [];
    const sender = new SteerSender(async (_turnId, content) => {
      sent.push(content);
      return { queued: false, steer_id: null };
    });
    const later = item("b");
    sender.send(item("a"));
    sender.send(later);
    sender.start("t1");
    await flush();
    await sender.settled();
    expect(sent).toEqual(["a"]);
    expect(later.steerId).toBeNull();
  });

  it("sends nothing once the request has ended", async () => {
    const sent: string[] = [];
    const sender = new SteerSender(async (_turnId, content) => {
      sent.push(content);
      return { queued: true, steer_id: "x" };
    });
    sender.send(item("late"));
    await sender.settled();
    expect(sent).toEqual([]);
  });
});
