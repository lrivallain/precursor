import { describe, expect, it } from "vitest";
import { latestOnly } from "./latestOnly";

describe("latestOnly", () => {
  it("drops an older answer that arrives after a newer one", () => {
    const order = latestOnly();
    const older = order.next();
    const newer = order.next();
    expect(order.accept(newer)).toBe(true); // the newer refresh lands first
    expect(order.accept(older)).toBe(false); // the slow, older one is ignored
  });

  it("applies answers that arrive in order", () => {
    const order = latestOnly();
    const first = order.next();
    expect(order.accept(first)).toBe(true);
    const second = order.next();
    expect(order.accept(second)).toBe(true);
  });
});
