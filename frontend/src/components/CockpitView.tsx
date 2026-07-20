import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ExternalLink,
  Gauge,
  Link2,
  Loader2,
  Pencil,
  Play,
  RotateCw,
  Square,
  Terminal,
  Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import type { Cockpit, CockpitState, CockpitStatus } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import { CockpitStateDot } from "./CockpitList";

const STATE_LABEL: Record<CockpitState, string> = {
  stopped: "Stopped",
  starting: "Starting…",
  running: "Running",
  unreachable: "Unreachable",
  crashed: "Crashed",
};

interface CockpitViewProps {
  cockpit: Cockpit;
  // Deep path within the embedded app to open initially (from the URL).
  initialPath?: string | null;
  onChanged: (id: number, status: CockpitStatus) => void;
  // Fired when the user navigates inside a same-origin (proxied command)
  // cockpit, so the parent can mirror the path into the app URL. Not called for
  // url cockpits (cross-origin — the browser blocks reading their location).
  onPathChange?: (path: string | null) => void;
  onUpdated: (cockpit: Cockpit) => void;
  onDeleted: () => void;
}

export function CockpitView({
  cockpit,
  initialPath = null,
  onChanged,
  onPathChange,
  onUpdated,
  onDeleted,
}: CockpitViewProps) {
  const confirmAction = useConfirm();
  const [status, setStatus] = useState<CockpitStatus>(cockpit.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logs, setLogs] = useState("");
  // Bumped to force the iframe to reload (e.g. after a restart).
  const [frameNonce, setFrameNonce] = useState(0);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  // The embedded app's current sub-path (after the proxy prefix), tracked for
  // same-origin command cockpits so Open-in-tab and the URL follow navigation.
  const currentPathRef = useRef<string | null>(initialPath);

  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const onPathChangeRef = useRef(onPathChange);
  onPathChangeRef.current = onPathChange;

  // Keep local status in step with the parent's copy when switching cockpits.
  useEffect(() => {
    setStatus(cockpit.status);
    setError(null);
    setLogsOpen(false);
    setLogs("");
  }, [cockpit.id, cockpit.status]);

  const applyStatus = useCallback(
    (next: CockpitStatus) => {
      setStatus((prev) => {
        if (prev.state !== next.state || prev.pid !== next.pid) {
          onChangedRef.current(cockpit.id, next);
        }
        return next;
      });
    },
    [cockpit.id],
  );

  // Poll live status while the view is open so the header + iframe react to the
  // process going ready, unreachable, or crashing on its own. URL cockpits have
  // no process, so there's nothing to poll.
  useEffect(() => {
    if (cockpit.kind !== "command") return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api.cockpits.status(cockpit.id);
        if (!cancelled) applyStatus(next);
      } catch {
        /* transient — keep the last known status */
      }
    };
    const timer = window.setInterval(tick, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [cockpit.id, cockpit.kind, applyStatus]);

  // Pull logs while the drawer is open (and always useful when crashed).
  useEffect(() => {
    if (!logsOpen) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const { logs: text } = await api.cockpits.logs(cockpit.id);
        if (!cancelled) setLogs(text);
      } catch {
        /* ignore */
      }
    };
    void tick();
    const timer = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [logsOpen, cockpit.id, status.state]);

  const isActive = status.state === "running" || status.state === "starting";
  const isUrl = cockpit.kind === "url";

  // Build the iframe src for a given deep path. Command cockpits go through the
  // same-origin reverse proxy; url cockpits embed the external URL directly.
  const buildSrc = useCallback(
    (path: string | null): string => {
      const rest = (path ?? "").replace(/^\/+/, "");
      if (isUrl) {
        const base = cockpit.url ?? "";
        return rest ? `${base.replace(/\/+$/, "")}/${rest}` : base;
      }
      return api.cockpits.proxyUrl(cockpit.id) + rest;
    },
    [isUrl, cockpit.url, cockpit.id],
  );

  // The src is set once at mount (with the initial deep path) and only changes
  // on restart — never from path-sync, which would reload the iframe in a loop.
  const [iframeSrc, setIframeSrc] = useState(() => buildSrc(initialPath));

  // If the target changes (e.g. a url cockpit's URL is edited), rebuild the src
  // at the current path so the iframe follows. No-op on mount (same value).
  useEffect(() => {
    setIframeSrc(buildSrc(currentPathRef.current));
  }, [buildSrc]);

  // Mirror in-app navigation into the URL. Only possible for command cockpits:
  // their proxied iframe is same-origin, so we can read its location. URL
  // cockpits are cross-origin and the browser blocks it.
  useEffect(() => {
    if (isUrl || status.state !== "running") return;
    const prefix = api.cockpits.proxyUrl(cockpit.id);
    const read = () => {
      const win = iframeRef.current?.contentWindow;
      if (!win) return;
      let pathname: string;
      try {
        pathname = win.location.pathname;
      } catch {
        return; // navigated cross-origin (external redirect) — can't read
      }
      if (!pathname.startsWith(prefix)) return;
      const norm = pathname.slice(prefix.length).replace(/^\/+/, "") || null;
      if (norm !== currentPathRef.current) {
        currentPathRef.current = norm;
        onPathChangeRef.current?.(norm);
      }
    };
    const el = iframeRef.current;
    el?.addEventListener("load", read);
    const timer = window.setInterval(read, 800);
    return () => {
      el?.removeEventListener("load", read);
      window.clearInterval(timer);
    };
  }, [isUrl, status.state, cockpit.id, frameNonce]);

  async function run(
    action: () => Promise<CockpitStatus>,
    opts: { reloadFrame?: boolean; resetPath?: boolean } = {},
  ): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const next = await action();
      applyStatus(next);
      if (opts.resetPath) {
        currentPathRef.current = null;
        setIframeSrc(buildSrc(null));
        onPathChangeRef.current?.(null);
      }
      if (opts.reloadFrame) setFrameNonce((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete "${cockpit.name}"? This stops the process and removes the cockpit.`,
        confirmLabel: "Delete cockpit",
        variant: "danger",
      }))
    )
      return;
    try {
      await api.cockpits.remove(cockpit.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function openInTab(): void {
    const rest = (currentPathRef.current ?? "").replace(/^\/+/, "");
    let target: string;
    if (isUrl) {
      const base = cockpit.url ?? "";
      target = rest ? `${base.replace(/\/+$/, "")}/${rest}` : base;
    } else {
      target = `http://localhost:${cockpit.port}/${rest}`;
    }
    if (target) window.open(target, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        {isUrl ? (
          <Link2 size={16} className="shrink-0 text-teal-600 dark:text-teal-400" />
        ) : (
          <Gauge size={16} className="shrink-0 text-teal-600 dark:text-teal-400" />
        )}
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{cockpit.name}</div>
          {isUrl ? (
            <div className="truncate font-mono text-xs text-muted" title={cockpit.url ?? ""}>
              {cockpit.url}
            </div>
          ) : (
            <div className="flex items-center gap-1.5 text-xs text-muted">
              <CockpitStateDot state={status.state} />
              <span>{STATE_LABEL[status.state]}</span>
              <span className="opacity-50">·</span>
              <span className="font-mono">:{cockpit.port}</span>
              {cockpit.autostart && (
                <>
                  <span className="opacity-50">·</span>
                  <span title="Starts automatically on app startup">autostart</span>
                </>
              )}
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1">
          {!isUrl &&
            (isActive ? (
              <HeaderButton
                onClick={() => run(() => api.cockpits.stop(cockpit.id))}
                busy={busy}
                icon={<Square size={14} />}
                label="Stop"
              />
            ) : (
              <HeaderButton
                onClick={() => run(() => api.cockpits.start(cockpit.id), { reloadFrame: true })}
                busy={busy}
                icon={<Play size={14} />}
                label="Start"
                primary
              />
            ))}
          {!isUrl && (
            <HeaderButton
              onClick={() =>
                run(() => api.cockpits.restart(cockpit.id), { reloadFrame: true, resetPath: true })
              }
              busy={busy}
              icon={<RotateCw size={14} />}
              label="Restart"
            />
          )}
          <HeaderButton onClick={openInTab} icon={<ExternalLink size={14} />} label="Open in tab" />
          {!isUrl && (
            <HeaderButton
              onClick={() => setLogsOpen((v) => !v)}
              icon={<Terminal size={14} />}
              label="Logs"
              active={logsOpen}
            />
          )}
          <HeaderButton onClick={() => setEditing(true)} icon={<Pencil size={14} />} label="Edit" />
          <HeaderButton onClick={remove} icon={<Trash2 size={14} />} label="Delete" danger />
        </div>
      </div>

      {error && (
        <div className="border-b border-border bg-red-500/10 px-4 py-1.5 text-xs text-red-500">
          {error}
        </div>
      )}

      <div className="relative flex-1 min-h-0 overflow-hidden">
        {isUrl ? (
          <iframe
            ref={iframeRef}
            title={cockpit.name}
            src={iframeSrc}
            className="h-full w-full border-0 bg-white"
          />
        ) : status.state === "running" ? (
          <iframe
            ref={iframeRef}
            key={frameNonce}
            title={cockpit.name}
            src={iframeSrc}
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <CockpitPlaceholder
            state={status.state}
            port={cockpit.port ?? 0}
            command={cockpit.command ?? ""}
            detail={status.detail}
            busy={busy}
            onStart={() => run(() => api.cockpits.start(cockpit.id), { reloadFrame: true })}
            onOpenTab={openInTab}
          />
        )}
      </div>

      {logsOpen && (
        <div className="h-48 shrink-0 overflow-auto border-t border-border bg-black/90 p-3">
          <pre className="whitespace-pre-wrap font-mono text-xs text-neutral-200">
            {logs || "No output yet."}
          </pre>
        </div>
      )}

      {editing && (
        <CockpitFormModal
          cockpit={cockpit}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setEditing(false);
            onUpdated(updated);
          }}
        />
      )}
    </div>
  );
}

function HeaderButton({
  onClick,
  icon,
  label,
  busy,
  primary,
  danger,
  active,
}: {
  onClick: () => void;
  icon: ReactNode;
  label: string;
  busy?: boolean;
  primary?: boolean;
  danger?: boolean;
  active?: boolean;
}) {
  const tone = primary
    ? "border-teal-500/30 bg-teal-500/15 text-teal-700 dark:text-teal-300 hover:bg-teal-500/25"
    : danger
      ? "border-border text-muted hover:bg-red-500/10 hover:text-red-500"
      : active
        ? "border-border bg-surface text-text"
        : "border-border text-muted hover:bg-surface hover:text-text";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={label}
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs disabled:opacity-50 ${tone}`}
    >
      {busy && primary ? <Loader2 size={14} className="animate-spin" /> : icon}
      <span className="hidden md:inline">{label}</span>
    </button>
  );
}

function CockpitPlaceholder({
  state,
  port,
  command,
  detail,
  busy,
  onStart,
  onOpenTab,
}: {
  state: CockpitState;
  port: number;
  command: string;
  detail: string | null;
  busy: boolean;
  onStart: () => void;
  onOpenTab: () => void;
}) {
  if (state === "starting") {
    return (
      <Centered>
        <Loader2 size={28} className="animate-spin text-muted" />
        <p className="text-sm text-muted">
          Starting… waiting for the app on port <span className="font-mono">{port}</span>.
        </p>
      </Centered>
    );
  }
  const failed = state === "crashed" || state === "unreachable";
  return (
    <Centered>
      {failed ? (
        <AlertTriangle size={28} className="text-amber-500" />
      ) : (
        <Gauge size={28} className="text-muted" />
      )}
      <div className="max-w-md space-y-1 text-center">
        <p className="text-sm font-medium">
          {state === "crashed"
            ? "The cockpit process exited."
            : state === "unreachable"
              ? "The cockpit didn't open its port."
              : "This cockpit isn't running."}
        </p>
        {detail && <p className="text-xs text-muted">{detail}</p>}
        <p className="font-mono text-xs text-muted break-all">
          {command} <span className="opacity-60">→ :{port}</span>
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onStart}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {failed ? "Retry" : "Start"}
        </button>
        <button
          type="button"
          onClick={onOpenTab}
          className="inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-sm text-muted hover:bg-surface hover:text-text"
        >
          <ExternalLink size={14} />
          Open in tab
        </button>
      </div>
    </Centered>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6">{children}</div>
  );
}

// --------------------------------------------------------------------------
// Create / edit modal
// --------------------------------------------------------------------------

// Serialize a KEY=VALUE-per-line textarea into the JSON object the API stores.
function envLinesToJson(text: string): string | null {
  const entries: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    entries[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return Object.keys(entries).length ? JSON.stringify(entries) : null;
}

function envJsonToLines(json: string | null): string {
  if (!json) return "";
  try {
    const obj = JSON.parse(json) as Record<string, string>;
    return Object.entries(obj)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");
  } catch {
    return "";
  }
}

export function CockpitFormModal({
  cockpit,
  onClose,
  onSaved,
}: {
  // When present, the modal edits an existing cockpit; otherwise it creates one.
  cockpit?: Cockpit;
  onClose: () => void;
  onSaved: (cockpit: Cockpit) => void;
}) {
  const isEdit = !!cockpit;
  const [kind, setKind] = useState<"command" | "url">(cockpit?.kind ?? "command");
  const [name, setName] = useState(cockpit?.name ?? "");
  const [command, setCommand] = useState(cockpit?.command ?? "");
  const [port, setPort] = useState(cockpit?.port != null ? String(cockpit.port) : "");
  const [cwd, setCwd] = useState(cockpit?.cwd ?? "");
  const [url, setUrl] = useState(cockpit?.url ?? "");
  const [description, setDescription] = useState(cockpit?.description ?? "");
  const [env, setEnv] = useState(envJsonToLines(cockpit?.env ?? null));
  const [autostart, setAutostart] = useState(cockpit?.autostart ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isUrl = kind === "url";
  const portNum = Number.parseInt(port, 10);
  const portValid = Number.isInteger(portNum) && portNum >= 1 && portNum <= 65535;
  const urlValid = /^https?:\/\//i.test(url.trim());
  const canSubmit =
    name.trim().length > 0 &&
    (isUrl ? urlValid : command.trim().length > 0 && portValid);

  async function submit(): Promise<void> {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const payload = isUrl
        ? {
            name: name.trim(),
            kind: "url" as const,
            url: url.trim(),
            description: description.trim() || null,
          }
        : {
            name: name.trim(),
            kind: "command" as const,
            command: command.trim(),
            port: portNum,
            cwd: cwd.trim() || null,
            description: description.trim() || null,
            env: envLinesToJson(env),
            autostart,
          };
      const saved = isEdit
        ? await api.cockpits.update(cockpit.id, payload)
        : await api.cockpits.create(payload);
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[32rem] max-w-[94vw] space-y-3 rounded-lg border border-border bg-bg p-5 shadow-xl">
        <h2 className="font-medium">{isEdit ? "Edit cockpit" : "New cockpit"}</h2>

        <div className="flex gap-1 rounded border border-border bg-surface p-0.5 text-sm">
          <button
            type="button"
            onClick={() => setKind("command")}
            aria-pressed={!isUrl}
            className={`flex-1 rounded px-2 py-1 ${
              !isUrl ? "bg-accent text-white" : "text-muted hover:text-text"
            }`}
          >
            Command
          </button>
          <button
            type="button"
            onClick={() => setKind("url")}
            aria-pressed={isUrl}
            className={`flex-1 rounded px-2 py-1 ${
              isUrl ? "bg-accent text-white" : "text-muted hover:text-text"
            }`}
          >
            URL
          </button>
        </div>

        <p className="text-xs text-muted">
          {isUrl ? (
            <>
              Embeds a fixed URL directly in an iframe — no process, no start/stop. Some sites
              refuse to be framed (e.g. ones sending <code>X-Frame-Options</code>); use “Open in
              tab” for those.
            </>
          ) : (
            <>
              Registers a local web app. On start, the command runs on your machine and Precursor
              embeds it once its port responds. The command runs with your privileges — only
              register cockpits you trust.
            </>
          )}
        </p>

        <label className="block text-sm">
          <span className="text-muted">Name</span>
          <input
            className="mt-1 w-full rounded border border-border bg-surface px-2 py-1.5 text-sm outline-none focus:border-accent"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={isUrl ? "Internal dashboard" : "Metrics dashboard"}
            autoFocus
          />
        </label>

        {isUrl ? (
          <label className="block text-sm">
            <span className="text-muted">URL</span>
            <input
              className="mt-1 w-full rounded border border-border bg-surface px-2 py-1.5 font-mono text-sm outline-none focus:border-accent"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://dashboard.internal.contoso.com"
            />
          </label>
        ) : (
          <>
            <label className="block text-sm">
              <span className="text-muted">Run command</span>
              <input
                className="mt-1 w-full rounded border border-border bg-surface px-2 py-1.5 font-mono text-sm outline-none focus:border-accent"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="npm run dev -- --port 5173"
              />
            </label>

            <div className="flex gap-3">
              <label className="block w-28 shrink-0 text-sm">
                <span className="text-muted">Port</span>
                <input
                  className="mt-1 w-full rounded border border-border bg-surface px-2 py-1.5 font-mono text-sm outline-none focus:border-accent"
                  value={port}
                  onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ""))}
                  placeholder="5173"
                  inputMode="numeric"
                />
              </label>
              <label className="block flex-1 text-sm">
                <span className="text-muted">Working directory (optional)</span>
                <input
                  className="mt-1 w-full rounded border border-border bg-surface px-2 py-1.5 font-mono text-sm outline-none focus:border-accent"
                  value={cwd}
                  onChange={(e) => setCwd(e.target.value)}
                  placeholder="/Users/me/projects/dashboard"
                />
              </label>
            </div>
          </>
        )}

        <label className="block text-sm">
          <span className="text-muted">Description (optional)</span>
          <input
            className="mt-1 w-full rounded border border-border bg-surface px-2 py-1.5 text-sm outline-none focus:border-accent"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What it does, how to use it…"
          />
        </label>

        {!isUrl && (
          <label className="block text-sm">
            <span className="text-muted">
              Environment variables (optional, one KEY=VALUE per line)
            </span>
            <textarea
              className="mt-1 h-20 w-full resize-none rounded border border-border bg-surface px-2 py-1.5 font-mono text-xs outline-none focus:border-accent"
              value={env}
              onChange={(e) => setEnv(e.target.value)}
              placeholder={"NODE_ENV=development\nAPI_URL=http://localhost:3000"}
            />
          </label>
        )}

        {!isUrl && (
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 accent-accent"
              checked={autostart}
              onChange={(e) => setAutostart(e.target.checked)}
            />
            <span>
              Start automatically on app startup
              <span className="block text-xs text-muted">
                Launched last in the startup order, best-effort.
              </span>
            </span>
          </label>
        )}

        {error && <p className="text-sm text-red-500">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <button
            className="rounded border border-border px-3 py-1.5 text-sm hover:bg-surface"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
            onClick={submit}
            disabled={busy || !canSubmit}
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            {isEdit ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
