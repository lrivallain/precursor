import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Send } from "lucide-react";
import { apiErrorMessage } from "../../lib/api";
import { Markdown } from "../Markdown";
import { BTN_SMALL, ErrorBanner } from "./StepUi";

/** One saved text of a step's artifact or of a plan's final output. */
export interface VersionRow {
  version: number;
  content: string;
  author: "user" | "model";
  created_at: string;
  /** Set when this version was filed into the topic. */
  posted_at?: string | null;
}

/**
 * The versions of a text, newest first: view one, or make it the current
 * version again (which never records a new one). `refreshKey` reloads the list
 * when the text moved.
 */
export function VersionHistory({
  load,
  refreshKey,
  current,
  onRestore,
}: {
  load: () => Promise<VersionRow[]>;
  refreshKey: unknown;
  current: number;
  onRestore: (v: VersionRow) => Promise<void>;
}) {
  const [versions, setVersions] = useState<VersionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<number | null>(null);
  const box = useRef<HTMLElement>(null);

  // It opens below the content, possibly out of view: bring it into view.
  useEffect(() => {
    box.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);

  const reload = useCallback(async () => {
    try {
      setVersions(await load());
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load the history."));
    }
    // `load` is a fresh closure every render; `refreshKey` says when to fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <section ref={box} className="space-y-2 rounded-lg border border-border p-3" aria-label="Version history">
      <h4 className="text-xs font-medium uppercase tracking-wide text-muted">Version history</h4>
      {error && <ErrorBanner message={error} />}
      {!versions && !error && (
        <p className="flex items-center gap-2 text-sm text-muted">
          <Loader2 size={14} className="animate-spin" /> Loading…
        </p>
      )}
      <ul className="space-y-1.5">
        {versions?.map((v) => (
          <li key={v.version} className="rounded-lg border border-border">
            <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-sm">
              <span className="font-medium">v{v.version}</span>
              <span className="text-xs text-muted">
                {v.author === "model" ? "model" : "you"} · {new Date(v.created_at).toLocaleString()}
              </span>
              {v.version === current && (
                <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] text-accent">current</span>
              )}
              {v.posted_at && (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-300"
                  data-tooltip={`Posted in the topic on ${new Date(v.posted_at).toLocaleString()}`}
                >
                  <Send size={10} /> posted
                </span>
              )}
              <span className="ml-auto flex gap-1.5">
                <button
                  type="button"
                  className={BTN_SMALL}
                  aria-expanded={shown === v.version}
                  onClick={() => setShown(shown === v.version ? null : v.version)}
                >
                  {shown === v.version ? "Hide" : "View"}
                </button>
                <button
                  type="button"
                  className={BTN_SMALL}
                  disabled={v.version === current}
                  data-tooltip={
                    v.version === current
                      ? "This is the current version"
                      : "Make this the current version (no new version is created)"
                  }
                  onClick={() => void onRestore(v)}
                >
                  Restore
                </button>
              </span>
            </div>
            {shown === v.version && (
              <div className="max-h-72 overflow-y-auto border-t border-border px-4 py-2">
                <Markdown>{v.content}</Markdown>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
