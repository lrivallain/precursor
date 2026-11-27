import { describe, expect, it } from "vitest";
import { localizeAppHref } from "./appLinks";

describe("localizeAppHref", () => {
  it("turns loopback links to app pages into paths", () => {
    expect(localizeAppHref("http://127.0.0.1:8000/topics/a/b")).toBe("/topics/a/b");
    expect(localizeAppHref("http://localhost:8899/chats/x?y=1#message=3")).toBe(
      "/chats/x?y=1#message=3",
    );
    expect(localizeAppHref("http://[::1]:8000/steps/4")).toBe("/steps/4");
    expect(localizeAppHref("http://127.0.0.1:8000/ws/notes")).toBe("/ws/notes");
  });

  it("leaves everything else alone", () => {
    expect(localizeAppHref(undefined)).toBeUndefined();
    expect(localizeAppHref("/topics/a")).toBe("/topics/a");
    expect(localizeAppHref("https://example.com/topics/a")).toBe("https://example.com/topics/a");
    // A local service that isn't one of the app's pages.
    expect(localizeAppHref("http://localhost:3000/")).toBe("http://localhost:3000/");
    expect(localizeAppHref("http://127.0.0.1:8000/api/openai/v1")).toBe(
      "http://127.0.0.1:8000/api/openai/v1",
    );
    expect(localizeAppHref("mailto:a@b.c")).toBe("mailto:a@b.c");
  });
});
