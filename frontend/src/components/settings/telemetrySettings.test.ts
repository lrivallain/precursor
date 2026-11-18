import { describe, expect, it } from "vitest";
import { changedTelemetry, withHeartbeat, type TelemetrySettings } from "./telemetrySettings";

const on: TelemetrySettings = { telemetry_enabled: true, telemetry_errors_enabled: true };
const off: TelemetrySettings = { telemetry_enabled: false, telemetry_errors_enabled: false };

describe("withHeartbeat", () => {
  it("turns error reports off with the heartbeat", () => {
    expect(withHeartbeat(on, false)).toEqual(off);
  });

  it("does not turn error reports on by itself", () => {
    expect(withHeartbeat(off, true)).toEqual({ telemetry_enabled: true, telemetry_errors_enabled: false });
  });
});

describe("changedTelemetry", () => {
  it("sends nothing when nothing changed, so no answer is recorded", () => {
    expect(changedTelemetry(off, { ...off })).toEqual({});
    expect(changedTelemetry(null, off)).toEqual({});
  });

  it("sends only the fields that changed", () => {
    expect(changedTelemetry(on, { ...on, telemetry_errors_enabled: false })).toEqual({
      telemetry_errors_enabled: false,
    });
    expect(changedTelemetry(on, off)).toEqual(off);
  });
});
