import { describe, expect, it } from "vitest";
import { changedUpdates, type UpdateSettings } from "./updateSettings";

const saved: UpdateSettings = {
  update_channel: "stable",
  update_checks_enabled: true,
  update_notifications_enabled: true,
};

describe("changedUpdates", () => {
  it("sends nothing when nothing changed, so the channel is not pinned", () => {
    expect(changedUpdates(saved, { ...saved })).toEqual({});
  });

  it("sends only the fields that changed", () => {
    expect(changedUpdates(saved, { ...saved, update_channel: "nightly", update_notifications_enabled: false })).toEqual({
      update_channel: "nightly",
      update_notifications_enabled: false,
    });
  });

  it("sends nothing before the settings have loaded", () => {
    expect(changedUpdates(null, saved)).toEqual({});
  });
});
