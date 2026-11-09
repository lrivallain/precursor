// The API transport: shared headers, error unwrapping, and `call()`, a request
// helper typed end to end from the backend's OpenAPI schema (the generated
// `Operations` map). `api.ts` builds its namespaces on top of it.
import type { Operations } from "./generated/apiTypes";
import { CLIENT_ID } from "./clientId";

/** An API route, as "METHOD /path" with the backend's `{param}` placeholders. */
export type OperationKey = keyof Operations;
/** What a successful `call(op)` resolves to. */
export type ResponseOf<K extends OperationKey> = Operations[K]["response"];
// Path params end up in the URL as text, so a string param (an agent's id or
// public uuid, say) also takes a number.
type Widen<P> = { [N in keyof P]: P[N] extends string ? string | number : P[N] };
type WithPath<O> = O extends { path: infer P } ? Omit<O, "path"> & { path: Widen<P> } : O;

/** The path params, query, and body (`body` JSON or `form` multipart) of `op`. */
export type ParamsOf<K extends OperationKey> = WithPath<Omit<Operations[K], "response">>;

/** Per-call options beside the operation's own params. */
export interface CallOptions {
  signal?: AbortSignal;
}

export type CallArgs<K extends OperationKey> = ParamsOf<K> & CallOptions;

// The args object may be left out only when nothing in it is required.
type CallRest<K extends OperationKey> =
  Record<string, never> extends ParamsOf<K> ? [args?: CallArgs<K>] : [args: CallArgs<K>];

type QueryValue = string | number | boolean | null | undefined;

/** Fill `{name}` placeholders, URL-encoding each value. */
export function buildPath(template: string, path?: Record<string, unknown>): string {
  return template.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = path?.[name];
    if (value === undefined || value === null) {
      throw new Error(`Missing path parameter "${name}" for ${template}`);
    }
    return encodeURIComponent(String(value));
  });
}

/**
 * Encode a query object: `null`/`undefined` (and empty arrays) are left out,
 * arrays repeat the key, booleans become "true"/"false". Returns "" or "?…".
 */
export function buildQuery(query?: Record<string, unknown>): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, raw] of Object.entries(query)) {
    const values = (Array.isArray(raw) ? raw : [raw]) as QueryValue[];
    for (const value of values) {
      if (value !== undefined && value !== null) params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** Split "METHOD /path" and build the URL from the args' path params and query. */
export function operationUrl(op: string, args?: { path?: unknown; query?: unknown }): {
  method: string;
  url: string;
} {
  const space = op.indexOf(" ");
  const method = op.slice(0, space);
  const template = op.slice(space + 1);
  const url =
    buildPath(template, args?.path as Record<string, unknown> | undefined) +
    buildQuery(args?.query as Record<string, unknown> | undefined);
  return { method, url };
}

function formData(fields: Record<string, unknown>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value instanceof File) form.append(key, value, value.name);
    else if (value instanceof Blob) form.append(key, value);
    else if (value !== undefined && value !== null) form.append(key, String(value));
  }
  return form;
}

/**
 * Call an API operation by its "METHOD /path" key. Path params, query, and body
 * are checked against the backend's schema, and the result is typed as the
 * route's response:
 *
 *     call("GET /api/topics/{topic_id}", { path: { topic_id: 1 } }) // Promise<TopicRead>
 */
export async function call<K extends OperationKey>(
  op: K,
  ...[args]: CallRest<K>
): Promise<ResponseOf<K>> {
  const params = args as { path?: unknown; query?: unknown; body?: unknown; form?: unknown } &
    CallOptions | undefined;
  const { method, url } = operationUrl(op, params);
  const init: RequestInit = { method, signal: params?.signal };
  const headers: Record<string, string> = { "X-Client-Id": CLIENT_ID };
  if (params?.form !== undefined) {
    init.body = formData(params.form as Record<string, unknown>);
  } else if (params?.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(params.body);
  }
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) throw await httpError(res);
  return (await readBody(res)) as ResponseOf<K>;
}

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined;
  const type = res.headers.get("Content-Type") ?? "";
  if (type && !type.includes("json")) return res.text();
  const text = await res.text();
  return text ? (JSON.parse(text) as unknown) : undefined;
}

/**
 * Issue a JSON API request with the shared headers + error unwrapping, for a
 * URL `call()` doesn't know. Re-exported through `@precursor/host` so plugin
 * bundles can call their own backend routes without re-implementing the
 * transport.
 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": CLIENT_ID,
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    throw await httpError(res);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// Pull a human-readable message out of an error response body. FastAPI wraps
// errors as `{"detail": "..."}` (or, for validation errors, a list of
// `{"msg", "loc"}` objects); return just that so the UI never shows the raw JSON
// envelope. Returns null when the body carries no usable detail.
function parseDetail(body: string): string | null {
  if (!body) return null;
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string") return detail.trim() || null;
      if (Array.isArray(detail)) {
        const msgs = detail
          .map((d) =>
            d && typeof d === "object" && typeof (d as { msg?: unknown }).msg === "string"
              ? (d as { msg: string }).msg
              : null,
          )
          .filter((m): m is string => m !== null);
        if (msgs.length) return msgs.join("; ");
      }
    }
  } catch {
    /* body isn't JSON — no detail to extract */
  }
  return null;
}

// Build an Error from a failed response: prefer the server's `detail` message
// (clean, user-facing) and fall back to the HTTP status when there's none.
export async function httpError(res: Response): Promise<Error> {
  const body = await res.text().catch(() => "");
  const detail = parseDetail(body);
  const err = new Error(
    detail ??
      (body ? `${res.status} ${res.statusText}: ${body}` : `${res.status} ${res.statusText}`),
  );
  // Lets callers tell an expected conflict (e.g. 409) from a real failure.
  return Object.assign(err, { status: res.status });
}

// FastAPI errors surface through `call`/`request` already reduced to their
// `detail` message. This helper stays as a safety net for any Error whose
// message still carries the "<status> <statusText>: <body>" envelope (e.g. from
// other call sites): it strips the prefix and returns the JSON detail if present.
export function apiErrorMessage(e: unknown, fallback = "Something went wrong"): string {
  if (!(e instanceof Error)) return fallback;
  const idx = e.message.indexOf(": ");
  const body = idx >= 0 ? e.message.slice(idx + 2) : e.message;
  return parseDetail(body) ?? (e.message || fallback);
}

/**
 * The shape a caller expects from a route whose response has no backend schema
 * yet (a `dict[str, Any]`, which the quality ratchet counts). Typing that route
 * in the backend makes `call()` return the real type and this wrapper can go.
 */
export function untyped<T>(response: Promise<unknown>): Promise<T> {
  return response as Promise<T>;
}
