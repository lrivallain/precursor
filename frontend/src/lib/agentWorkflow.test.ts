import { describe, expect, it } from "vitest";
import { assistant, ev, user } from "./agentTestEvents";
import {
  buildRows,
  computeElapsedByEvent,
  computeModelByEvent,
  rowAlign,
  segmentRows,
} from "./agentWorkflow";

describe("buildRows", () => {
  it("merges a tool's start and completion into one step", () => {
    const rows = buildRows([
      ev("tool_execution_start", { tool_name: "bash", request_id: "t1", data: { arguments: "ls" } }),
      ev("tool_execution_complete", { request_id: "t1", data: { result: "a b" } }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      type: "tool",
      step: { toolName: "bash", input: "ls", output: "a b", done: true },
    });
  });

  it("places a step at its start even when the completion arrives first", () => {
    const rows = buildRows([
      ev("tool_execution_complete", { tool_name: "bash", request_id: "t1" }),
      assistant("calling"),
      ev("tool_execution_start", { tool_name: "bash", request_id: "t1" }),
    ]);
    expect(rows.map((r) => r.type)).toEqual(["node", "tool"]);
  });

  it("still renders a step whose start never arrived", () => {
    const rows = buildRows([ev("tool_execution_complete", { tool_name: "bash", request_id: "t1" })]);
    expect(rows).toEqual([{ type: "tool", step: expect.objectContaining({ done: true }) }]);
  });

  it("marks an unfinished call gated by a following permission echo", () => {
    const rows = buildRows([
      ev("tool_execution_start", { tool_name: "bash", request_id: "t1" }),
      ev("permissionrequested"),
    ]);
    expect(rows[0]).toMatchObject({ type: "tool", step: { gated: true } });
  });

  it("parks a pending approval on its step", () => {
    const rows = buildRows([
      ev("permission_request", { request_id: "p1", data: { kind: "shell" } }),
    ]);
    expect(rows[0]).toMatchObject({
      type: "tool",
      step: { pending: { data: { kind: "shell" }, requestId: "p1" } },
    });
  });

  it("attaches a round's thinking to the message before it", () => {
    const rows = buildRows([assistant("answer"), ev("reasoning", { text: "hmm" })]);
    expect(rows).toEqual([expect.objectContaining({ type: "node", cat: "assistant", reasoning: "hmm" })]);
  });

  it("keeps thinking standalone after a new turn starts", () => {
    const rows = buildRows([assistant("answer"), ev("turn_start"), ev("reasoning", { text: "hmm" })]);
    expect(rows.filter((r) => r.type === "node").map((r) => r.type === "node" && r.cat)).toEqual([
      "assistant",
      "reasoning",
    ]);
  });

  it("drops blank assistant markers, encrypted reasoning and streaming deltas", () => {
    const rows = buildRows([
      assistant("  "),
      ev("reasoning", { text: "" }),
      ev("assistant_message_delta", { text: "par" }),
    ]);
    expect(rows).toEqual([]);
  });
});

describe("computeElapsedByEvent", () => {
  it("measures each answer from its turn's prompt", () => {
    const answer = assistant("done", "2026-01-01T00:00:05Z");
    const map = computeElapsedByEvent([user("go", "2026-01-01T00:00:00Z"), answer]);
    expect(map.get(answer)).toBe(5000);
  });

  it("skips answers with no anchor", () => {
    const answer = assistant("done", "2026-01-01T00:00:05Z");
    expect(computeElapsedByEvent([answer]).has(answer)).toBe(false);
  });
});

describe("computeModelByEvent", () => {
  it("tags answers with the turn's usage model and resets per turn", () => {
    const first = assistant("a");
    const second = assistant("b");
    const map = computeModelByEvent([
      ev("usage", { data: { model: "gpt-x" } }),
      first,
      ev("turn_start"),
      second,
    ]);
    expect(map.get(first)).toBe("gpt-x");
    expect(map.has(second)).toBe(false);
  });
});

describe("segmentRows", () => {
  it("attaches hooks to the next row and keeps the trailing ones", () => {
    const hook = ev("session_idle");
    const { segments, trailingHooks } = segmentRows(buildRows([hook, user("hi"), hook]));
    expect(segments).toHaveLength(1);
    expect(segments[0].hooks).toEqual([hook]);
    expect(trailingHooks).toEqual([hook]);
  });
});

describe("rowAlign", () => {
  const nudge = buildRows([user("Continue working autonomously toward your objective.")])[0];

  it("puts prompts right, answers left, the rest centred", () => {
    expect(rowAlign(buildRows([user("hi")])[0], { centerNudge: true })).toBe("justify-end");
    expect(rowAlign(buildRows([assistant("yo")])[0], { centerNudge: true })).toBe("justify-start");
    expect(rowAlign(buildRows([ev("session_idle")])[0], { centerNudge: true })).toBe("justify-center");
  });

  it("centres the goal-loop nudge only when asked", () => {
    expect(rowAlign(nudge, { centerNudge: true })).toBe("justify-center");
    expect(rowAlign(nudge, { centerNudge: false })).toBe("justify-end");
  });
});
