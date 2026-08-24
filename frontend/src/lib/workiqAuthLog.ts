/**
 * Console tracing for the WorkIQ re-authentication legs.
 *
 * The hands-free re-auth is a single ``/reauthenticate?auto=true`` request that
 * hides *two* sequential attempts inside it — the invisible ``prompt=none``
 * iframe (leg ①) and, when that can't complete, the backend self-opening the OS
 * default browser (leg ②). From the SPA both look like one long POST, so when
 * the banner finally appears there is no way to tell which leg gave up, or why.
 *
 * This traces the observable boundaries around that request — when it started,
 * when the authorization URL arrived over SSE (published for leg ① only, so its
 * absence is itself a signal), how the hidden frame navigated, and how long each
 * step took — which is enough to separate the failure modes:
 *
 * - returns in well under a second with no ``auth url`` line → the backend never
 *   started a leg (auto re-auth disabled, loopback port busy, no stored account
 *   to use as ``login_hint``);
 * - ``auth url`` then ~20s of silence → leg ① reached Entra but its loopback
 *   never fired, i.e. the frame couldn't complete without UI;
 * - a further long wait after that → leg ② is driving the OS browser (it
 *   deliberately does not publish its URL), and a machine whose default browser
 *   or profile differs from the one running the SPA can never complete it.
 *
 * On by default because it only ever fires during an auth episode, which is
 * rare. Silence it with ``localStorage['precursor.debug.workiqAuth'] = '0'``.
 * Every line is also kept in a small ring buffer that
 * ``window.precursorWorkiqAuthTrace()`` returns, so a whole episode can be
 * copied out of the console in one go.
 *
 * The SPA only sees half the story, though — the legs it *observes* rather than
 * the ones it drives. ``window.precursorWorkiqAuthReport()`` closes that gap: it
 * pulls the backend's own trace and credential fact sheet from
 * ``GET /api/mcp/auth/diagnostics`` and returns both halves in one object, keyed
 * by the backend's episode ids. That is the artefact worth pasting into an
 * issue, and the reason the backend stamps ``auth_episode`` onto its re-auth
 * responses: a line here and a line there can be put on the same timeline.
 */

import { api } from "./api";
import { mcpAuthFamily } from "./mcpServers";
import type { McpAuthDiagnostics } from "./types";

const STORAGE_KEY = "precursor.debug.workiqAuth";
const PREFIX = "[workiq-auth]";
const TRACE_LIMIT = 200;

export interface AuthTraceEntry {
  at: string;
  server: string;
  phase: string;
  elapsedMs: number | null;
  detail?: Record<string, unknown>;
}

const trace: AuthTraceEntry[] = [];

// Per-*family* stopwatches: the two Agent 365 servers share one credential and
// therefore one episode, so timing them separately would double-count it.
const clocks = new Map<string, number>();

function enabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "0";
  } catch {
    // Storage blocked (private mode, embedded context) — tracing is harmless.
    return true;
  }
}

/** Start (or restart) the stopwatch for ``server``'s credential. */
export function authLogStart(server: string): void {
  clocks.set(mcpAuthFamily(server), performance.now());
}

/** Start the stopwatch only if this credential's episode isn't already timed. */
export function authLogEnsure(server: string): void {
  const family = mcpAuthFamily(server);
  if (!clocks.has(family)) clocks.set(family, performance.now());
}

/** Stop the stopwatch once an episode is over, so the next one starts at zero. */
export function authLogEnd(server: string): void {
  clocks.delete(mcpAuthFamily(server));
}

/** Record one step of an auth episode, timestamped against its stopwatch. */
export function authLog(
  server: string,
  phase: string,
  detail?: Record<string, unknown>,
): void {
  const started = clocks.get(mcpAuthFamily(server));
  const elapsedMs = started === undefined ? null : Math.round(performance.now() - started);

  trace.push({ at: new Date().toISOString(), server, phase, elapsedMs, detail });
  if (trace.length > TRACE_LIMIT) trace.shift();

  if (!enabled()) return;
  const stamp = elapsedMs === null ? "" : ` +${elapsedMs}ms`;
  const line = `${PREFIX} ${server}${stamp} — ${phase}`;
  if (detail) console.info(line, detail);
  else console.info(line);
}

/**
 * Summarize an Entra authorization URL for the log.
 *
 * Deliberately omits ``login_hint`` (the user's own address), ``state`` and
 * ``nonce``: presence is all that's diagnostic, and the trace is meant to be
 * pasteable into a bug report. ``client_id`` and ``redirect_uri`` are kept —
 * they identify *which* credential and *which* loopback port a leg is using,
 * which is exactly what a port-busy or wrong-credential episode turns on.
 */
export function describeAuthUrl(url: string): Record<string, unknown> {
  try {
    const parsed = new URL(url);
    const params = parsed.searchParams;
    return {
      host: parsed.host,
      path: parsed.pathname,
      prompt: params.get("prompt") ?? "(unset)",
      has_login_hint: params.has("login_hint"),
      client_id: params.get("client_id"),
      redirect_uri: params.get("redirect_uri"),
    };
  } catch {
    return { url: "(unparseable)" };
  }
}

/**
 * Peek at where the hidden silent frame actually ended up after a navigation.
 *
 * A cross-origin document throws on property access, which here is the *good*
 * outcome: it means Entra (or the loopback) really served the frame. A readable
 * ``about:blank`` instead means the navigation was refused before any document
 * loaded — the signature of ``X-Frame-Options``/``frame-ancestors`` blocking the
 * silent pass outright, which no amount of SSO cookie will fix.
 */
export function probeFrame(frame: HTMLIFrameElement): Record<string, unknown> {
  try {
    const href = frame.contentWindow?.location.href;
    return { readable: true, href: href ?? "(none)", verdict: "likely blocked before load" };
  } catch {
    return { readable: false, verdict: "cross-origin document (frame did load)" };
  }
}

declare global {
  interface Window {
    precursorWorkiqAuthTrace?: () => AuthTraceEntry[];
    precursorWorkiqAuthReport?: () => Promise<WorkiqAuthReport>;
  }
}

/** Both halves of an auth investigation: what the SPA saw and what the backend did. */
export interface WorkiqAuthReport {
  capturedAt: string;
  /** The SPA's own ring buffer (this window only). */
  frontend: AuthTraceEntry[];
  /** ``GET /api/mcp/auth/diagnostics`` — settings, credential facts, backend trace. */
  backend: McpAuthDiagnostics | { error: string };
}

/**
 * Collect one pasteable report covering an entire auth episode.
 *
 * The SPA and the backend each hold half of it, and neither half is conclusive
 * alone: the SPA knows the iframe never loaded, the backend knows Entra answered
 * ``AADSTS700082``. Fetching the backend side on demand (rather than streaming
 * it continuously) keeps this free until someone actually investigates.
 *
 * Never rejects — a backend that can't answer is itself a finding, so the error
 * is recorded in place of the diagnostics.
 */
export async function collectAuthReport(): Promise<WorkiqAuthReport> {
  let backend: McpAuthDiagnostics | { error: string };
  try {
    backend = await api.mcp.authDiagnostics();
  } catch (err) {
    backend = { error: (err as Error).message };
  }
  return { capturedAt: new Date().toISOString(), frontend: [...trace], backend };
}

if (typeof window !== "undefined") {
  window.precursorWorkiqAuthTrace = () => [...trace];
  window.precursorWorkiqAuthReport = collectAuthReport;
}
