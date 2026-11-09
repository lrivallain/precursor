import { afterEach, describe, expect, it, vi } from "vitest";
import { buildPath, buildQuery, call, operationUrl, untyped } from "./apiClient";

interface Sent {
  url: string;
  init: RequestInit;
}

function mockFetch(response: Response): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
    sent.push({ url, init });
    return Promise.resolve(response);
  });
  return sent;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("buildPath", () => {
  it("fills placeholders and encodes values", () => {
    expect(buildPath("/api/topics/{topic_id}", { topic_id: 7 })).toBe("/api/topics/7");
    expect(buildPath("/api/topics/by-slug/{slug}", { slug: "a b/c" })).toBe(
      "/api/topics/by-slug/a%20b%2Fc",
    );
  });

  it("fills several placeholders", () => {
    expect(
      buildPath("/api/chats/{chat_id}/messages/{message_id}", { chat_id: 1, message_id: 2 }),
    ).toBe("/api/chats/1/messages/2");
  });

  it("throws on a missing value", () => {
    expect(() => buildPath("/api/topics/{topic_id}", {})).toThrow(/topic_id/);
    expect(() => buildPath("/api/topics/{topic_id}")).toThrow(/topic_id/);
  });
});

describe("buildQuery", () => {
  it("is empty without values", () => {
    expect(buildQuery()).toBe("");
    expect(buildQuery({})).toBe("");
    expect(buildQuery({ a: undefined, b: null, c: [] })).toBe("");
  });

  it("encodes strings, numbers and booleans", () => {
    expect(buildQuery({ q: "a&b c", limit: 5, force: true, off: false })).toBe(
      "?q=a%26b+c&limit=5&force=true&off=false",
    );
  });

  it("keeps an empty string and repeats array keys", () => {
    expect(buildQuery({ q: "" })).toBe("?q=");
    expect(buildQuery({ id: [1, 2] })).toBe("?id=1&id=2");
  });
});

describe("operationUrl", () => {
  it("splits the method and builds the URL", () => {
    expect(
      operationUrl("GET /api/agents/{agent_id}/runs", {
        path: { agent_id: "abc" },
        query: { limit: 3 },
      }),
    ).toEqual({ method: "GET", url: "/api/agents/abc/runs?limit=3" });
  });
});

describe("call", () => {
  it("sends method, URL and client id, and parses JSON", async () => {
    const sent = mockFetch(json({ id: 3 }));
    const topic = await call("GET /api/topics/{topic_id}", { path: { topic_id: 3 } });
    expect(topic).toEqual({ id: 3 });
    expect(sent[0].url).toBe("/api/topics/3");
    expect(sent[0].init.method).toBe("GET");
    const headers = sent[0].init.headers as Record<string, string>;
    expect(headers["X-Client-Id"]).toBeTruthy();
    expect(headers["Content-Type"]).toBeUndefined();
    expect(sent[0].init.body).toBeUndefined();
  });

  it("sends a JSON body with its content type", async () => {
    const sent = mockFetch(json({ id: 1 }, 201));
    await call("POST /api/chats", { body: { title: "Hi" } });
    expect(sent[0].init.method).toBe("POST");
    expect(sent[0].init.body).toBe('{"title":"Hi"}');
    expect((sent[0].init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
  });

  it("sends a multipart form without forcing a content type", async () => {
    const sent = mockFetch(json({ id: 9 }));
    const file = new File(["data"], "notes.txt", { type: "text/plain" });
    await call("POST /api/topics/{topic_id}/attachments", {
      path: { topic_id: 4 },
      form: { file },
    });
    const body = sent[0].init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("notes.txt");
    expect((sent[0].init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("passes the abort signal through", async () => {
    const sent = mockFetch(json({}));
    const controller = new AbortController();
    await call("GET /api/step-plans/{plan_id}", {
      path: { plan_id: 1 },
      signal: controller.signal,
    });
    expect(sent[0].init.signal).toBe(controller.signal);
  });

  it("resolves 204 to undefined", async () => {
    mockFetch(new Response(null, { status: 204 }));
    await expect(call("DELETE /api/topics/{topic_id}", { path: { topic_id: 1 } })).resolves.toBe(
      undefined,
    );
  });

  it("returns text for a non-JSON response", async () => {
    mockFetch(new Response("name: x\n", { headers: { "Content-Type": "text/plain" } }));
    await expect(
      call("GET /api/skills/{name}/export", { path: { name: "x" } }),
    ).resolves.toBe("name: x\n");
  });

  it("throws the server's detail with the status", async () => {
    mockFetch(json({ detail: "Topic not found" }, 404));
    const error = await call("GET /api/topics/{topic_id}", { path: { topic_id: 1 } }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("Topic not found");
    expect((error as Error & { status: number }).status).toBe(404);
  });

  it("joins validation error messages", async () => {
    mockFetch(json({ detail: [{ msg: "field required" }, { msg: "too long" }] }, 422));
    await expect(call("POST /api/chats", { body: { title: "" } })).rejects.toThrow(
      "field required; too long",
    );
  });

  it("falls back to the status line without a detail", async () => {
    mockFetch(new Response("", { status: 500, statusText: "Internal Server Error" }));
    await expect(call("GET /api/settings")).rejects.toThrow("500 Internal Server Error");
  });
});

describe("call types", () => {
  it("rejects wrong params at compile time", () => {
    mockFetch(json({}));
    const checks = [
      // @ts-expect-error -- the path param is required
      () => call("GET /api/topics/{topic_id}"),
      // @ts-expect-error -- wrong path param name
      () => call("GET /api/topics/{topic_id}", { path: { id: 1 } }),
      // @ts-expect-error -- not a route
      () => call("GET /api/nope"),
      // @ts-expect-error -- the body doesn't match the schema
      () => call("POST /api/chats", { body: { title: 1 } }),
      // Optional query: the args object may be left out.
      () => call("GET /api/topics"),
      // A string path param also takes a number.
      () => call("GET /api/agents/{agent_id}", { path: { agent_id: 1 } }),
    ];
    expect(checks).toHaveLength(6);
  });
});

describe("untyped", () => {
  it("passes the value through", async () => {
    await expect(untyped<{ ok: boolean }>(Promise.resolve({ ok: true }))).resolves.toEqual({
      ok: true,
    });
  });
});
