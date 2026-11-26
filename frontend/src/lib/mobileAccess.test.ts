import { describe, expect, it } from "vitest";
import { needsNewCode, pollDelay, tunnelSummary } from "./mobileAccess";
import type { PairingCode } from "./remoteAccessApi";

const NOW = Date.parse("2026-11-26T10:00:00Z");

function code(expiresIn: number, url: string | null = "https://t/x"): PairingCode {
  return {
    path: "/api/remote/pair?code=x",
    expires_at: new Date(NOW + expiresIn).toISOString(),
    url,
    qr_svg: url ? "<svg/>" : null,
  };
}

describe("needsNewCode", () => {
  it("wants a code when there is none or no tunnel URL yet", () => {
    expect(needsNewCode(null, NOW)).toBe(true);
    expect(needsNewCode(code(20 * 60_000, null), NOW)).toBe(true);
  });
  it("keeps a fresh code and replaces one about to expire", () => {
    expect(needsNewCode(code(20 * 60_000), NOW)).toBe(false);
    expect(needsNewCode(code(30_000), NOW)).toBe(true);
    expect(needsNewCode(code(-1), NOW)).toBe(true);
  });
});

describe("pollDelay", () => {
  it("polls faster while the tunnel starts", () => {
    expect(pollDelay("starting")).toBeLessThan(pollDelay("active"));
  });
});

describe("tunnelSummary", () => {
  it("shows the tunnel's own error", () => {
    expect(tunnelSummary({ state: "error", url: null, error: "boom" })).toBe("boom");
  });
  it("has a line for every state", () => {
    for (const state of ["missing_cli", "signed_out", "stopped", "starting", "active"] as const) {
      expect(tunnelSummary({ state, url: null, error: null })).not.toBe("");
    }
  });
});
