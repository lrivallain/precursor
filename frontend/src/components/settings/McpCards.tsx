import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

// Precursor capability sections served over the built-in "precursor" MCP
// server, with a short label + whether they perform writes.
export const EXPOSE_SECTIONS: ReadonlyArray<{
  key: string;
  label: string;
  hint: string;
  danger?: boolean;
}> = [
  { key: "topics", label: "Topics", hint: "List & read topic metadata." },
  { key: "messages", label: "Messages", hint: "Read a topic's conversation turns." },
  { key: "chats", label: "Chats", hint: "List & read chats and their turns." },
  {
    key: "agents",
    label: "Agents",
    hint: "List & read agent sessions (task prompt & final answer).",
  },
  {
    key: "live",
    label: "Live sessions",
    hint: "List & read meeting notes, summary, transcript & insights.",
  },
  {
    key: "search",
    label: "Search",
    hint: "Search across topics, chats, agents & live sessions.",
  },
  {
    key: "iq",
    label: "IQ retrieve",
    hint: "Ranked passages with citations (retrieve). Each hit also needs its own section on.",
  },
  {
    key: "iq_ask",
    label: "IQ ask",
    hint: "Cited answers written by your model (ask) — uses provider quota per call.",
  },
  { key: "skills", label: "Skills", hint: "List skills & read their instructions." },
  { key: "memory", label: "Memory", hint: "Read long-term memory entries." },
  {
    key: "memory_write",
    label: "Edit memory",
    hint: "Let callers add and edit long-term memory entries.",
    danger: true,
  },
  {
    key: "agent_state",
    label: "Agent state",
    hint: "Read & write an agent's durable scratchpad (cursors it keeps between runs).",
    danger: true,
  },
  {
    key: "workflow_state",
    label: "Workflow state",
    hint: "Read & write a workflow's shared pipeline memory.",
    danger: true,
  },
  {
    key: "notes",
    label: "Append notes",
    hint: "Let callers file text into a topic verbatim, without running a turn.",
    danger: true,
  },
  {
    key: "post_message",
    label: "Post message",
    hint: "Let callers post to a topic and run a full assistant turn.",
    danger: true,
  },
  {
    key: "side_chats",
    label: "Side chats",
    hint: "Let callers start a chat linked to a topic or one of its replies.",
    danger: true,
  },
  {
    key: "step_plans",
    label: "Step-by-step plans",
    hint: "Let callers split a long job into a plan, run it and follow it (never approves steps).",
    danger: true,
  },
  {
    key: "schedules",
    label: "Scheduled tasks",
    hint: "List, create, pause and trigger recurring automations.",
    danger: true,
  },
  {
    key: "reminders",
    label: "Reminders",
    hint: "List, set and cancel one-shot topic reminders.",
    danger: true,
  },
  {
    key: "workspaces",
    label: "Workspaces",
    hint: "List workspaces and read their files.",
  },
  {
    key: "workspaces_write",
    label: "Edit workspaces",
    hint: "Let callers create workspaces (local or git clone) and write files in them. Agents still ask before each write.",
    danger: true,
  },
  {
    key: "tools",
    label: "Basic tools",
    hint: "Small helpers that read none of your data: date and time, calculator, unit conversion, UUIDs, hashing, text counts.",
  },
];

export const GUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Tenant for the Agent 365 hosted MCP servers (the ``workiq-*`` family): their
// URL embeds a tenant GUID, so without one they stay unconfigured. Left blank,
// Precursor reads the tenant off an existing WorkIQ sign-in.
export function Agent365Card({
  tenant,
  setTenant,
  discoveredTenant,
}: {
  tenant: string;
  setTenant: (next: string) => void;
  discoveredTenant: string;
}) {
  const [open, setOpen] = useState(false);
  const typed = tenant.trim();
  const effective = typed || discoveredTenant;
  const invalid = typed !== "" && !GUID_RE.test(typed);
  return (
    <div className="border border-border rounded mb-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 w-full px-2 py-1.5 text-left"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="text-sm flex-1">
          Microsoft 365 tenant (Agent 365 servers)
        </span>
        <span className="text-[11px] text-muted">
          {effective ? (typed ? "set" : "discovered") : "not set"}
        </span>
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2 space-y-2">
          <p className="text-[11px] text-muted">
            The hosted <span className="font-mono">workiq-teams</span>,{" "}
            <span className="font-mono">workiq-user</span>,{" "}
            <span className="font-mono">workiq-planner</span>,{" "}
            <span className="font-mono">workiq-word</span> and{" "}
            <span className="font-mono">workiq-excel</span> servers address your
            tenant by GUID. Leave this blank to reuse the tenant of an existing
            WorkIQ sign-in.
          </p>
          <input
            type="text"
            value={tenant}
            onChange={(e) => setTenant(e.target.value)}
            placeholder={discoveredTenant || "00000000-0000-0000-0000-000000000000"}
            spellCheck={false}
            className="w-full px-2 py-1 rounded border border-border bg-surface text-sm font-mono"
          />
          {invalid && (
            <div className="text-[11px] rounded border border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400 px-3 py-2">
              Expected a tenant GUID like{" "}
              <span className="font-mono">72f988bf-86f1-41af-91ab-2d7cd011db47</span>.
            </div>
          )}
          {!typed && discoveredTenant && (
            <div className="text-[11px] text-muted">
              Using{" "}
              <span className="font-mono text-text/80">{discoveredTenant}</span>,
              discovered from your WorkIQ sign-in.
            </div>
          )}
          {!effective && (
            <div className="text-[11px] text-muted">
              Until a tenant is known those two servers stay unconfigured. Save
              settings to apply.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Browser channel the built-in "playwright" MCP server drives (--browser).
export const PLAYWRIGHT_BROWSER_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "default", label: "Default (let @playwright/mcp choose)" },
  { value: "msedge", label: "Microsoft Edge" },
  { value: "chromium", label: "Chromium" },
  { value: "chrome", label: "Google Chrome" },
  { value: "firefox", label: "Firefox" },
  { value: "webkit", label: "WebKit" },
];

export function PlaywrightCard({
  browser,
  setBrowser,
}: {
  browser: string;
  setBrowser: (next: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const label =
    PLAYWRIGHT_BROWSER_OPTIONS.find((o) => o.value === browser)?.label ?? browser;
  return (
    <div className="border border-border rounded mb-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 w-full px-2 py-1.5 text-left"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="text-sm flex-1">Playwright browser</span>
        <span className="text-[11px] text-muted">{label}</span>
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2 space-y-2">
          <p className="text-[11px] text-muted">
            The built-in <span className="font-mono">playwright</span> server
            launches this browser (passed as{" "}
            <span className="font-mono">--browser</span>). Edge is the default so
            it can ride the corporate SSO broker. Save settings to apply.
          </p>
          <select
            value={browser}
            onChange={(e) => setBrowser(e.target.value)}
            className="w-full px-2 py-1 rounded border border-border bg-surface text-sm"
          >
            {PLAYWRIGHT_BROWSER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {browser === "default" && (
            <div className="text-[11px] text-muted">
              <span className="font-mono">--browser</span> is omitted entirely.
              Use this if the server fails to start with{" "}
              <span className="font-mono">unknown option '--browser'</span> — some
              builds of <span className="font-mono">@playwright/mcp</span> predate
              the flag.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Per-section enablement for the built-in "precursor" MCP server, which serves
// Precursor's own capabilities to the in-app agent and external MCP hosts.
export function McpExposeCard({
  expose,
  setExpose,
  precursorEnabled,
  httpEnabled,
  setHttpEnabled,
  httpUrl,
  httpLoopbackOk,
}: {
  expose: Record<string, boolean>;
  setExpose: (next: Record<string, boolean>) => void;
  precursorEnabled: boolean;
  httpEnabled: boolean;
  setHttpEnabled: (next: boolean) => void;
  httpUrl: string | null;
  httpLoopbackOk: boolean;
}) {
  const [open, setOpen] = useState(false);
  const anyOn = EXPOSE_SECTIONS.some((s) => expose[s.key]);
  const allOn = EXPOSE_SECTIONS.every((s) => expose[s.key]);
  const anyWriteOn = EXPOSE_SECTIONS.some((s) => s.danger && expose[s.key]);
  const setAll = (on: boolean) =>
    setExpose({
      ...expose,
      ...Object.fromEntries(EXPOSE_SECTIONS.map((s) => [s.key, on])),
    });
  return (
    <div className="border border-border rounded mb-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 w-full px-2 py-1.5 text-left"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="text-sm flex-1">Precursor capabilities (serve over MCP)</span>
        <span className="text-[11px] text-muted">
          {anyOn ? "some exposed" : "none exposed"}
        </span>
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2 space-y-2">
          <p className="text-[11px] text-muted">
            Expose Precursor's own features through the built-in{" "}
            <span className="font-mono">precursor</span> MCP server — usable by
            this app's assistant and by external MCP hosts. Everything is off by
            default. Toggling a section here doesn't enable the server itself —
            flip the <span className="font-mono">precursor</span> server below
            for the in-app agent to use it.
          </p>
          <div className="flex items-center gap-3 text-[11px]">
            <button
              type="button"
              onClick={() => setAll(true)}
              disabled={allOn}
              className="text-accent hover:underline disabled:opacity-40 disabled:no-underline"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={() => setAll(false)}
              disabled={!anyOn}
              className="text-accent hover:underline disabled:opacity-40 disabled:no-underline"
            >
              Unselect all
            </button>
          </div>
          {EXPOSE_SECTIONS.map((s) => (
            <label key={s.key} className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={!!expose[s.key]}
                onChange={(e) => setExpose({ ...expose, [s.key]: e.target.checked })}
                className="mt-0.5 accent-accent"
              />
              <span className="min-w-0">
                <span className="block text-sm">
                  {s.label}
                  {s.danger && (
                    <span className="ml-1.5 text-[10px] text-amber-600 dark:text-amber-400 border border-amber-500/40 rounded px-1">
                      write
                    </span>
                  )}
                </span>
                <span className="block text-[11px] text-muted">{s.hint}</span>
              </span>
            </label>
          ))}
          {anyWriteOn && (
            <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
              Write capabilities let a caller change this Precursor instance
              (post messages, manage schedules, create workspaces and write files). Only enable for trusted hosts.
            </div>
          )}
          {anyOn && !precursorEnabled && (
            <div className="text-[11px] text-muted">
              Note: the in-app assistant won't use these until you enable the{" "}
              <span className="font-mono">precursor</span> server below.
            </div>
          )}

          {/* HTTP transport — connect external hosts without launching a
              subprocess. Localhost-only, unauthenticated. */}
          <div className="border-t border-border pt-2 mt-2 space-y-2">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={httpEnabled}
                disabled={!httpLoopbackOk}
                onChange={(e) => setHttpEnabled(e.target.checked)}
                className="mt-0.5 accent-accent"
              />
              <span className="min-w-0">
                <span className="block text-sm">Serve over HTTP (localhost)</span>
                <span className="block text-[11px] text-muted">
                  Also expose the <span className="font-mono">precursor</span>{" "}
                  server at a local URL so hosts can connect without spawning a
                  subprocess. Unauthenticated; only answers on the loopback bind.
                </span>
              </span>
            </label>
            {!httpLoopbackOk && (
              <div className="text-[11px] rounded border border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400 px-3 py-2">
                The app is bound to a non-loopback host. The HTTP transport stays
                off because it has no authentication. Bind to{" "}
                <span className="font-mono">127.0.0.1</span> to use it.
              </div>
            )}
            {httpEnabled && httpLoopbackOk && httpUrl && (
              <div className="text-[11px] text-muted">
                Endpoint:{" "}
                <span className="font-mono text-text/80 break-all">{httpUrl}</span>{" "}
                (streamable-http). Save settings to apply.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
