import { describe, expect, it, vi } from "vitest";
import { Emitter, ListResourceStore, VersionedStore } from "./externalStore";

describe("Emitter", () => {
  it("notifies subscribers and bumps the version on every emit", () => {
    const emitter = new Emitter();
    const listener = vi.fn();
    emitter.subscribe(listener);
    expect(emitter.getVersion()).toBe(0);
    emitter.emit();
    emitter.emit();
    expect(listener).toHaveBeenCalledTimes(2);
    expect(emitter.getVersion()).toBe(2);
  });

  it("stops notifying after unsubscribe", () => {
    const emitter = new Emitter();
    const listener = vi.fn();
    const unsubscribe = emitter.subscribe(listener);
    unsubscribe();
    emitter.emit();
    expect(listener).not.toHaveBeenCalled();
  });

  it("keeps working when its methods are destructured", () => {
    const { subscribe, emit, getVersion } = new Emitter();
    const listener = vi.fn();
    subscribe(listener);
    emit();
    expect(listener).toHaveBeenCalledOnce();
    expect(getVersion()).toBe(1);
  });
});

class Counter extends VersionedStore {
  value = 0;
  increment(): void {
    this.value++;
    this.notify();
  }
}

describe("VersionedStore", () => {
  it("exposes a snapshot that changes on notify", () => {
    const store = new Counter();
    const listener = vi.fn();
    store.subscribe(listener);
    const before = store.getSnapshot();
    store.increment();
    expect(store.getSnapshot()).not.toBe(before);
    expect(listener).toHaveBeenCalledOnce();
    expect(store.value).toBe(1);
  });
});

class Names extends ListResourceStore<string> {
  protected readonly label = "names";
  calls = 0;
  constructor(private readonly result: () => Promise<string[]>) {
    super();
  }
  protected fetchAll(): Promise<string[]> {
    this.calls++;
    return this.result();
  }
}

describe("ListResourceStore", () => {
  it("loads once with ensureLoaded and notifies", async () => {
    const store = new Names(async () => ["a", "b"]);
    const listener = vi.fn();
    store.subscribe(listener);
    await store.ensureLoaded();
    await store.ensureLoaded();
    expect(store.all()).toEqual(["a", "b"]);
    expect(store.calls).toBe(1);
    expect(listener).toHaveBeenCalledOnce();
  });

  it("keeps the previous list and retries later when a fetch fails", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    let fail = true;
    const store = new Names(async () => {
      if (fail) throw new Error("offline");
      return ["x"];
    });
    await store.ensureLoaded();
    expect(store.all()).toEqual([]);
    expect(error).toHaveBeenCalledWith("Failed to load names", expect.any(Error));
    fail = false;
    await store.ensureLoaded();
    expect(store.all()).toEqual(["x"]);
    error.mockRestore();
  });
});
