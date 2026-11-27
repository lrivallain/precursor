import { describe, expect, it } from "vitest";
import { singleFlight } from "./singleFlight";

describe("singleFlight", () => {
  it("shares the running request with overlapping callers", async () => {
    let calls = 0;
    let release!: (value: string) => void;
    const mint = singleFlight(() => {
      calls += 1;
      return new Promise<string>((resolve) => (release = resolve));
    });
    const first = mint(); // e.g. the polling tick
    const second = mint(); // "Add a mobile device" clicked meanwhile
    release("code-1");
    expect(await first).toBe("code-1");
    expect(await second).toBe("code-1");
    expect(calls).toBe(1);
  });

  it("starts a new request once the previous one has settled", async () => {
    let calls = 0;
    const mint = singleFlight(async () => `code-${++calls}`);
    expect(await mint()).toBe("code-1");
    expect(await mint()).toBe("code-2");
  });

  it("lets the next call retry after a failure", async () => {
    let calls = 0;
    const mint = singleFlight(async () => {
      calls += 1;
      if (calls === 1) throw new Error("offline");
      return "ok";
    });
    await expect(mint()).rejects.toThrow("offline");
    expect(await mint()).toBe("ok");
  });
});
