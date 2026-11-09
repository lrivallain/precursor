import { describe, expect, it } from "vitest";
import { messageModel, toolStatus } from "./agentNodeModel";
import { DEFAULT_SHOW_PREFS, parseShowPrefs } from "./agentShowPrefs";

describe("messageModel", () => {
  const plain = { assistant: true, autonomy: false, offerSuggestions: false };

  it("passes non-assistant text through", () => {
    expect(messageModel("PROGRESS: 10%", { ...plain, assistant: false })).toMatchObject({
      bodyText: "PROGRESS: 10%",
      directives: null,
      heartbeatOnly: false,
    });
  });

  it("leaves directives alone on a normal agent", () => {
    expect(messageModel("PROGRESS: 10%", plain).bodyText).toBe("PROGRESS: 10%");
  });

  it("strips directives on an autonomy agent and spots a heartbeat", () => {
    const m = messageModel("PROGRESS: 10% scanning", { ...plain, autonomy: true });
    expect(m.bodyText).toBe("");
    expect(m.heartbeatOnly).toBe(true);
  });

  it("folds a completion summary in as the body", () => {
    const m = messageModel("OBJECTIVE_COMPLETE: shipped it", { ...plain, autonomy: true });
    expect(m).toMatchObject({ isCompletion: true, bodyText: "shipped it", heartbeatOnly: false });
  });

  it("only offers suggestions when asked", () => {
    const text = "Done.\n```suggest\nShip it\n```";
    expect(messageModel(text, plain).suggestions).toEqual([]);
    expect(messageModel(text, { ...plain, offerSuggestions: true }).suggestions.length).toBeGreaterThan(0);
  });
});

describe("toolStatus", () => {
  it("labels the step's state", () => {
    expect(toolStatus({ key: "a", toolName: null }, false)).toMatchObject({ label: "running", running: true, alert: false });
    expect(toolStatus({ key: "a", toolName: null, done: true }, true).label).toBe("done");
    expect(toolStatus({ key: "a", toolName: null, pending: { data: {}, requestId: null } }, false)).toMatchObject({
      label: "awaiting approval",
      alert: true,
    });
  });

  it("reports a step a closed attempt left behind", () => {
    expect(toolStatus({ key: "a", toolName: null }, true)).toMatchObject({
      label: "interrupted — attempt ended",
      stale: true,
      alert: true,
      running: false,
    });
    expect(toolStatus({ key: "a", toolName: null, gated: true }, true).label).toBe("never approved — attempt ended");
  });
});

describe("parseShowPrefs", () => {
  it("fills in defaults and rejects a corrupt blob", () => {
    expect(parseShowPrefs('{"system":true}')).toEqual({ ...DEFAULT_SHOW_PREFS, system: true });
    expect(parseShowPrefs("{nope")).toBeNull();
  });
});
