import { describe, expect, it } from "vitest";
import {
  agentStatusFlags,
  buildTurnGroup,
  describeRun,
  followUpKind,
  latestMcpAuth,
  mergeEventPage,
  pendingLanded,
  resolvePane,
  sameOr,
  userPromptHistory,
  withRewindableIds,
} from "./agentCockpitModel";
import type { ResultVersion } from "./agentResults";
import { assistant, ev, user } from "./agentTestEvents";
import type { AgentEventPage, AgentRun } from "./types";
import type { AgentTurn } from "./useAgentRewind";

const NUDGE = "Continue working autonomously toward your objective.";

const run = (id: number, over: Partial<AgentRun> = {}): AgentRun =>
  ({ id, trigger: "manual", workflow_run_id: null, ...over }) as AgentRun;

const turn = (i: number, runId: number | null, eventId: string | null = "e"): AgentTurn & { agent_run_id: number | null } => ({
  message_id: i,
  last_message_id: i,
  created_at: "",
  prompt: "",
  reply: "",
  has_compaction: false,
  agent_session_id: null,
  event_id: eventId,
  text: "",
  agent_run_id: runId,
});

describe("agentStatusFlags", () => {
  it("derives the cockpit's status flags", () => {
    expect(agentStatusFlags("running")).toMatchObject({ turnActive: true, terminal: false, needsYou: false });
    expect(agentStatusFlags("needs_approval")).toMatchObject({ turnActive: true, needsYou: true });
    expect(agentStatusFlags("idle")).toMatchObject({ turnActive: false, terminal: true, atRest: true });
    expect(agentStatusFlags("failed")).toMatchObject({ terminal: true, atRest: false });
    expect(agentStatusFlags(null)).toMatchObject({ turnActive: false, terminal: false });
  });
});

describe("describeRun", () => {
  it("names the trigger and the workflow run", () => {
    expect(describeRun(run(3))).toBe("Run #3 · Manual");
    expect(describeRun(run(3, { trigger: "workflow", workflow_run_id: 9 }))).toBe(
      "Run #3 · Workflow · workflow run #9",
    );
  });
});

describe("pendingLanded", () => {
  it("matches the newest human prompt, skipping the nudge", () => {
    expect(pendingLanded([user(" hi "), assistant("x"), user(NUDGE)], "hi")).toBe(true);
    expect(pendingLanded([user("hi"), user("other")], "hi")).toBe(false);
    expect(pendingLanded([], "hi")).toBe(false);
  });
});

describe("userPromptHistory", () => {
  it("lists human prompts only", () => {
    expect(userPromptHistory([user("a"), assistant("b"), user(NUDGE), user("")])).toEqual(["a"]);
  });
});

describe("latestMcpAuth", () => {
  it("reports the newest sign-in request with fallbacks", () => {
    expect(latestMcpAuth([assistant("x")])).toBeNull();
    expect(latestMcpAuth([ev("mcp_auth_required")])).toEqual({ server: "workiq", message: "Sign-in required." });
    expect(
      latestMcpAuth([ev("mcp_auth_required", { data: { server: "a" } }), ev("mcp_auth_required", { tool_name: "b", text: "go" })]),
    ).toEqual({ server: "b", message: "go" });
  });
});

describe("mergeEventPage", () => {
  const page = (events: ReturnType<typeof user>[], reset = false): AgentEventPage =>
    ({ events, pending: [], thinking: null, cursor: 0, reset, epoch: "", rewindable_run_ids: [] });
  const prev = [user("a")];

  it("appends a delta and keeps identity when it's empty", () => {
    expect(mergeEventPage(prev, page([user("b")]), false)).toHaveLength(2);
    expect(mergeEventPage(prev, page([]), false)).toBe(prev);
  });

  it("replaces the history on a reset or a fresh read", () => {
    const next = [user("z")];
    expect(mergeEventPage(prev, page(next, true), false)).toBe(next);
    expect(mergeEventPage(prev, page(next), true)).toBe(next);
  });
});

describe("sameOr", () => {
  it("keeps the previous array when the values match", () => {
    const prev = [1, 2];
    expect(sameOr(prev, [1, 2])).toBe(prev);
    expect(sameOr(prev, [1, 3])).toEqual([1, 3]);
  });
});

describe("withRewindableIds", () => {
  it("clears the event id of turns outside the current session", () => {
    const out = withRewindableIds([turn(0, 1), turn(1, 2), turn(2, null)], [2]);
    expect(out.map((t) => t.event_id)).toEqual([null, "e", null]);
  });
});

describe("buildTurnGroup", () => {
  it("is off for a single run", () => {
    expect(buildTurnGroup([turn(0, 1), turn(1, 1)], [run(1)], [])).toBeUndefined();
  });

  it("labels each run and the current session", () => {
    const group = buildTurnGroup([turn(0, null), turn(1, 1), turn(2, 2)], [run(1)], [2]);
    expect(group?.(0)).toEqual({ key: "none", label: "Before runs were recorded" });
    expect(group?.(1)).toEqual({ key: 1, label: "Run #1 · Manual" });
    expect(group?.(2)).toEqual({ key: 2, label: "Run #2 · current session" });
  });
});

describe("followUpKind", () => {
  it("routes slash commands", () => {
    expect(followUpKind("/clear")).toBe("clear");
    expect(followUpKind("/role ")).toBe("role");
    expect(followUpKind("/role reviewer")).toBe("command");
    expect(followUpKind("fix it")).toBe("prompt");
  });
});

describe("resolvePane", () => {
  const version = (index: number | null): ResultVersion => ({
    n: 1,
    exchange: index == null ? null : { index, at: null, prompt: null, summary: null },
    artifacts: [],
    at: "",
  });
  const anchors = [{ index: 0, at: null, prompt: null, summary: null }, { index: 1, at: null, prompt: null, summary: null }];
  const base = { choice: null, anchors, atRest: true, showPending: false, linked: false };

  it("follows the result once a fresh one is in and the agent rests", () => {
    expect(resolvePane({ ...base, latestVersion: version(1) })).toBe("result");
    expect(resolvePane({ ...base, latestVersion: version(0) })).toBe("activity");
    expect(resolvePane({ ...base, latestVersion: version(1), atRest: false })).toBe("activity");
    expect(resolvePane({ ...base, latestVersion: version(1), showPending: true })).toBe("activity");
  });

  it("honours a pick, unless there's no result or a link is open", () => {
    expect(resolvePane({ ...base, latestVersion: version(1), choice: "activity" })).toBe("activity");
    expect(resolvePane({ ...base, latestVersion: null, choice: "result" })).toBe("activity");
    expect(resolvePane({ ...base, latestVersion: version(1), linked: true })).toBe("activity");
  });
});
