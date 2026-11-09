import { describe, expect, it } from "vitest";
import { buildCockpitTimeline, excerpt, type CockpitTimelineOptions } from "./agentCockpitTimeline";
import { DEFAULT_SHOW_PREFS } from "./agentShowPrefs";
import { assistant, ev, idle, user } from "./agentTestEvents";

const opts = (over: Partial<CockpitTimelineOptions> = {}): CockpitTimelineOptions => ({
  showPrefs: DEFAULT_SHOW_PREFS,
  terminal: true,
  live: false,
  autonomy: false,
  expanded: new Set(),
  ...over,
});

const kinds = (t: ReturnType<typeof buildCockpitTimeline>): string[] =>
  t.segments.map(({ entry }) =>
    entry.kind === "fold" ? "fold" : `${entry.role}:${entry.row.type}`,
  );

// prompt → tool → chatter → answer, then idle.
const finishedTurn = [
  user("do it", "2026-01-01T00:00:00Z", { event_id: "e1", agent_run_id: 7 }),
  ev("tool_execution_start", { tool_name: "bash", request_id: "t1" }),
  ev("tool_execution_complete", { request_id: "t1" }),
  assistant("working on it"),
  assistant("all done", "2026-01-01T00:01:00Z"),
  idle(),
];

describe("buildCockpitTimeline", () => {
  it("folds a finished turn's work between its prompt and answer", () => {
    const t = buildCockpitTimeline(finishedTurn, opts());
    expect(kinds(t)).toEqual(["prompt:node", "fold", "answer:node"]);
    const fold = t.segments[1].entry;
    expect(fold.kind === "fold" && fold.stats).toEqual({
      durationMs: 60000,
      tools: 1,
      thoughts: 0,
      messages: 1,
      errors: 0,
    });
    expect(t.latestAnswerKey).toBe(t.segments[2].entry.key);
    // The idle marker is part of the folded work.
    expect(t.trailingHooks).toHaveLength(0);
  });

  it("shows the work inline once the fold is expanded", () => {
    const t = buildCockpitTimeline(finishedTurn, opts({ expanded: new Set([0]) }));
    expect(kinds(t)).toEqual(["prompt:node", "fold", "work:tool", "work:node", "answer:node"]);
  });

  it("keeps the live turn open", () => {
    const t = buildCockpitTimeline(finishedTurn, opts({ live: true }));
    expect(kinds(t)).toEqual(["prompt:node", "work:tool", "work:node", "answer:node"]);
  });

  it("keeps everything inline when folding is off and hides what the prefs hide", () => {
    const t = buildCockpitTimeline(
      finishedTurn,
      opts({ showPrefs: { ...DEFAULT_SHOW_PREFS, fold: false, tool: false, assistant: false } }),
    );
    // Assistant chatter is hidden, the answer stays.
    expect(kinds(t)).toEqual(["prompt:node", "answer:node"]);
  });

  it("treats the last message before a later prompt as that turn's answer", () => {
    const t = buildCockpitTimeline(
      [user("one"), assistant("a1"), user("two"), assistant("a2")],
      opts({ terminal: false }),
    );
    expect(kinds(t)).toEqual(["prompt:node", "answer:node", "prompt:node", "work:node"]);
    expect(t.exchangeStart).toEqual(new Map([[0, 0], [1, 2]]));
  });

  it("describes each prompt as a rail turn and a result anchor", () => {
    const t = buildCockpitTimeline(finishedTurn, opts());
    expect(t.turns).toEqual([
      expect.objectContaining({
        message_id: 0,
        prompt: "do it",
        reply: "all done",
        event_id: "e1",
        agent_run_id: 7,
        text: "do it",
      }),
    ]);
    expect(t.anchors).toEqual([
      { index: 0, at: "2026-01-01T00:00:00Z", prompt: "do it", summary: null },
    ]);
  });

  it("does not open a new exchange for the goal loop's nudge", () => {
    const t = buildCockpitTimeline(
      [user("go"), assistant("step"), user("Continue working autonomously toward your objective.")],
      opts({ terminal: false }),
    );
    expect(t.turns).toHaveLength(1);
  });
});

describe("excerpt", () => {
  it("flattens whitespace and truncates", () => {
    expect(excerpt("a\n  b")).toBe("a b");
    expect(excerpt("abcdef", 4)).toBe("abc…");
    expect(excerpt(null)).toBe("");
  });
});
