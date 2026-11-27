import { describe, expect, it, vi } from "vitest";
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

describe("remote context", () => {
  it("announces the context once it arrives, so mounted links re-render", async () => {
    const { remoteApi } = await import("./remoteAccessApi");
    const ctx = await import("./mobileAccess");
    ctx.resetRemoteContext();
    let resolve!: (v: { remote: boolean; app_origin: string }) => void;
    const spy = vi
      .spyOn(remoteApi, "context")
      .mockReturnValue(new Promise((r) => (resolve = r)) as never);
    let notified = 0;
    const unsubscribe = ctx.remoteContextChanges.subscribe(() => notified++);
    const loading = ctx.loadRemoteContext();
    // Mounted before the answer: nothing known yet.
    expect(ctx.remoteContextSnapshot()).toEqual({ remote: null, appOrigin: null });
    resolve({ remote: true, app_origin: "http://127.0.0.1:8000" });
    await loading;
    expect(notified).toBe(1);
    expect(ctx.remoteContextSnapshot()).toEqual({
      remote: true,
      appOrigin: "http://127.0.0.1:8000",
    });
    unsubscribe();
    spy.mockRestore();
  });

  it("stays unknown after a failure and asks again", async () => {
    const { remoteApi } = await import("./remoteAccessApi");
    const ctx = await import("./mobileAccess");
    ctx.resetRemoteContext();
    const spy = vi
      .spyOn(remoteApi, "context")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ remote: false, app_origin: "http://127.0.0.1:8000" } as never);
    expect(await ctx.loadRemoteContext()).toBeNull();
    expect(ctx.remoteContextSnapshot().remote).toBeNull();
    expect(await ctx.loadRemoteContext()).toBe(false);
    expect(spy).toHaveBeenCalledTimes(2);
    spy.mockRestore();
  });
});
