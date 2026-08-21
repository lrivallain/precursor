import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Loader2, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import type { DrawioStatus } from "../lib/types";

// Embed flags: `embed=1&proto=json` turns on the postMessage protocol,
// `offline=1&stealth=1` keep the editor from reaching any external origin
// (the whole point of self-hosting it), and the chrome we replace with the
// workspace toolbar is hidden.
const EMBED_PARAMS = [
  "embed=1",
  "proto=json",
  "spin=1",
  "offline=1",
  "stealth=1",
  "libraries=1",
  "noSaveBtn=1",
  "noExitBtn=1",
];

function editorUrl(dark: boolean): string {
  const params = [...EMBED_PARAMS, `dark=${dark ? 1 : 0}`];
  return `/drawio/index.html?${params.join("&")}`;
}

function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

function formatMb(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(0)} MB`;
}

/**
 * Edit a `.drawio` diagram with the self-hosted diagrams.net editor.
 *
 * The XML round-trips through the caller: `xml` seeds the editor once per file
 * and every change comes back through `onChange`, so the workspace's existing
 * dirty marker and Save button keep working unchanged. The webapp itself is
 * downloaded on demand — until it is installed this renders the install
 * prompt instead of the editor.
 */
export function DrawioEditor({
  path,
  xml,
  onChange,
}: {
  path: string;
  xml: string;
  onChange: (xml: string) => void;
}) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState<DrawioStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const dark = isDark();

  // The editor is seeded once per file; later `xml` changes are echoes of the
  // editor's own edits and must not reload it (that would reset the viewport
  // and the undo stack on every keystroke).
  const pendingXml = useRef(xml);
  pendingXml.current = xml;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const refreshStatus = useCallback(async () => {
    try {
      const next = await api.drawio.status();
      setStatus(next);
      setStatusError(null);
      return next;
    } catch (e) {
      setStatusError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // Poll while a download is in flight so the progress bar advances.
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => {
      void refreshStatus().then((next) => {
        if (next && (next.installed || next.error)) setInstalling(false);
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [installing, refreshStatus]);

  const install = async () => {
    setInstalling(true);
    try {
      setStatus(await api.drawio.install());
    } catch {
      setInstalling(false);
    }
  };

  const installed = status?.installed ?? false;

  useEffect(() => {
    if (!installed) return;
    const onMessage = (event: MessageEvent) => {
      const frame = frameRef.current;
      if (!frame || event.source !== frame.contentWindow) return;
      if (typeof event.data !== "string" || !event.data.startsWith("{")) return;

      let msg: { event?: string; xml?: string };
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      const post = (payload: unknown) =>
        frame.contentWindow?.postMessage(JSON.stringify(payload), "*");

      if (msg.event === "init") {
        // `autosave` makes the editor stream every change back to us rather
        // than waiting for an explicit save inside its own UI.
        post({ action: "load", xml: pendingXml.current, autosave: 1 });
        return;
      }
      if ((msg.event === "autosave" || msg.event === "save") && msg.xml != null) {
        onChangeRef.current(msg.xml);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [installed]);

  if (status && !status.installed) {
    const total = status.total_bytes;
    const pct = total ? Math.round((status.downloaded_bytes / total) * 100) : 0;
    const busy = installing || status.step !== "idle";
    return (
      <div className="h-full flex items-center justify-center p-6">
        <div className="max-w-md text-center space-y-3">
          <p className="text-sm">
            Diagram editing uses a self-hosted copy of draw.io, so nothing in{" "}
            <code className="text-xs">{path}</code> leaves this machine.
          </p>
          <p className="text-xs text-muted">
            It isn't installed yet — {status.version} is a one-time ~53 MB
            download (~150 MB on disk) into {status.path}.
          </p>
          {status.error && (
            <p className="text-xs text-red-500">Install failed: {status.error}</p>
          )}
          {busy ? (
            <div className="space-y-2">
              <div className="h-1.5 rounded bg-surface overflow-hidden">
                <div
                  className="h-full bg-accent transition-[width]"
                  style={{ width: `${status.step === "extract" ? 100 : pct}%` }}
                />
              </div>
              <p className="text-xs text-muted inline-flex items-center gap-1.5">
                <Loader2 size={13} className="animate-spin" />
                {status.step === "extract"
                  ? "Extracting…"
                  : `Downloading… ${formatMb(status.downloaded_bytes)}${
                      total ? ` / ${formatMb(total)}` : ""
                    }`}
              </p>
            </div>
          ) : (
            <button
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent text-white text-xs"
              onClick={() => void install()}
            >
              {status.error ? <RefreshCw size={13} /> : <Download size={13} />}
              {status.error ? "Retry install" : "Install the diagram editor"}
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!installed) {
    return (
      <div className="h-full flex items-center justify-center text-muted text-sm">
        {statusError ? (
          <span className="text-red-500">
            Couldn't reach the diagram editor: {statusError}
          </span>
        ) : (
          <Loader2 className="animate-spin" size={18} />
        )}
      </div>
    );
  }

  return (
    <iframe
      // Remount on file switch so the editor reloads with the new diagram.
      key={`${path}:${dark}`}
      ref={frameRef}
      title={path}
      src={editorUrl(dark)}
      className="w-full h-full border-0 bg-white"
    />
  );
}
