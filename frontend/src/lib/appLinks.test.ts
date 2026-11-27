import { describe, expect, it } from "vitest";
import { localizeAppHref } from "./appLinks";

const APP = "http://127.0.0.1:8000";

describe("localizeAppHref", () => {
  it("turns loopback links to this app's pages into paths", () => {
    expect(localizeAppHref("http://127.0.0.1:8000/topics/a/b", APP)).toBe("/topics/a/b");
    expect(localizeAppHref("http://localhost:8000/chats/x?y=1#message=3", APP)).toBe(
      "/chats/x?y=1#message=3",
    );
    expect(localizeAppHref("http://[::1]:8000/steps/4", APP)).toBe("/steps/4");
    expect(localizeAppHref("http://127.0.0.1:8000/ws/notes", APP)).toBe("/ws/notes");
  });

  it("keeps links to another local service or another instance", () => {
    expect(localizeAppHref("http://localhost:3000/topics/a", APP)).toBe(
      "http://localhost:3000/topics/a",
    );
    expect(localizeAppHref("http://127.0.0.1:8001/chats/x", APP)).toBe(
      "http://127.0.0.1:8001/chats/x",
    );
  });

  it("does nothing until the app's origin is known", () => {
    expect(localizeAppHref("http://127.0.0.1:8000/topics/a", null)).toBe(
      "http://127.0.0.1:8000/topics/a",
    );
  });

  it("leaves everything else alone", () => {
    expect(localizeAppHref(undefined, APP)).toBeUndefined();
    expect(localizeAppHref("/topics/a", APP)).toBe("/topics/a");
    expect(localizeAppHref("https://example.com/topics/a", APP)).toBe(
      "https://example.com/topics/a",
    );
    expect(localizeAppHref("http://127.0.0.1:8000/api/openai/v1", APP)).toBe(
      "http://127.0.0.1:8000/api/openai/v1",
    );
    expect(localizeAppHref("mailto:a@b.c", APP)).toBe("mailto:a@b.c");
  });
});
