import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import type { IQStatus, Settings } from "../lib/types";

interface Props {
  settings: Settings | null;
  onSettings: (next: Settings) => void;
}

const BACKEND_LABEL: Record<string, string> = {
  fts5: "SQLite FTS5 (BM25)",
  tsvector: "Postgres full-text (tsvector)",
  like: "substring scan (no full-text index)",
};

/**
 * Settings → MCP card for Precursor IQ: index health, a manual re-index, and
 * the opt-in embeddings. Like the OpenAI endpoint card, changes apply at once
 * rather than on the panel's Save — they drive background work the user wants
 * to see start.
 */
export function IQSettings({ settings, onSettings }: Props) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<IQStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState(settings?.iq_embedding_model ?? "");
  const [askModel, setAskModel] = useState(settings?.iq_ask_model ?? "");

  useEffect(() => {
    setModel(settings?.iq_embedding_model ?? "");
    setAskModel(settings?.iq_ask_model ?? "");
  }, [settings?.iq_embedding_model, settings?.iq_ask_model]);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.iq.status());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll while there's indexing work in flight so the counters move.
  const embeddingBacklog =
    !!status?.embeddings_enabled &&
    !!status.embeddings_available &&
    status.embedded < status.chunks;
  const working = open && !!status && (status.pending > 0 || embeddingBacklog);
  useEffect(() => {
    if (!working) return;
    const handle = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(handle);
  }, [working, refresh]);

  if (!settings) return null;
  const current = settings;

  async function save(patch: Parameters<typeof api.settings.update>[0]): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      onSettings(await api.settings.update(patch));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reindex(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      setStatus(await api.iq.reindex());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const summary = !status
    ? "…"
    : !status.enabled
      ? "disabled"
      : status.pending > 0
        ? `indexing · ${status.pending} queued`
        : `${status.chunks.toLocaleString()} passages`;

  return (
    <div className="border border-border rounded mb-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 w-full px-2 py-1.5 text-left"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="text-sm flex-1">Precursor IQ (semantic retrieval)</span>
        <span className="text-[11px] text-muted">{summary}</span>
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2 space-y-3">
          <p className="text-[11px] text-muted">
            Indexes your topics, briefs, messages, attachments, chats, agent
            runs, live notes and memory so the ⌘K palette ranks by relevance
            and can <span className="font-medium">Ask</span> for a cited
            answer. Expose <span className="font-mono">IQ retrieve</span> /{" "}
            <span className="font-mono">IQ ask</span> above to offer the same
            to MCP hosts.
          </p>

          {status && !status.enabled && (
            <div className="text-[11px] rounded border border-border bg-surface px-3 py-2 text-muted">
              Disabled by <span className="font-mono">PRECURSOR_IQ_ENABLED=false</span>.
              The palette falls back to plain substring search.
            </div>
          )}

          {status && status.enabled && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
              <dt className="text-muted">Passages</dt>
              <dd>{status.chunks.toLocaleString()}</dd>
              <dt className="text-muted">Queued changes</dt>
              <dd>{status.pending.toLocaleString()}</dd>
              <dt className="text-muted">Full-text engine</dt>
              <dd>{BACKEND_LABEL[status.lexical_backend] ?? status.lexical_backend}</dd>
              <dt className="text-muted">With embeddings</dt>
              <dd>
                {status.embeddings_enabled
                  ? `${status.embedded.toLocaleString()} / ${status.chunks.toLocaleString()}`
                  : "off"}
              </dd>
            </dl>
          )}

          <button
            type="button"
            onClick={() => void reindex()}
            disabled={busy || !status?.enabled}
            className="flex items-center gap-1.5 rounded border border-border px-2 py-1 text-xs hover:bg-surface disabled:opacity-50"
          >
            <RefreshCw size={12} className={working ? "animate-spin" : ""} />
            Rebuild index
          </button>

          <div className="border-t border-border pt-2 space-y-2">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={current.iq_embeddings_enabled}
                disabled={busy}
                onChange={(e) => void save({ iq_embeddings_enabled: e.target.checked })}
                className="mt-0.5 accent-accent"
              />
              <span className="min-w-0">
                <span className="block text-sm">Semantic matching (embeddings)</span>
                <span className="block text-[11px] text-muted">
                  Also match paraphrases, not just shared words. Every indexed
                  passage is sent to your model provider's embeddings endpoint
                  once, which uses its quota.
                </span>
              </span>
            </label>
            {current.iq_embeddings_enabled && status && !status.embeddings_available && (
              <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
                The active provider has no embeddings endpoint; retrieval stays
                full-text only.
              </div>
            )}
            <label className="block text-[11px] text-muted">
              Embeddings model (deployment name on Azure)
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                onBlur={() => {
                  if (model.trim() && model.trim() !== current.iq_embedding_model) {
                    void save({ iq_embedding_model: model.trim() });
                  }
                }}
                placeholder="text-embedding-3-small"
                spellCheck={false}
                className="mt-0.5 w-full px-2 py-1 rounded border border-border bg-surface text-sm font-mono text-text"
              />
            </label>
            <label className="block text-[11px] text-muted">
              Answer model (blank = your chat model)
              <input
                type="text"
                value={askModel}
                onChange={(e) => setAskModel(e.target.value)}
                onBlur={() => {
                  if (askModel.trim() !== current.iq_ask_model) {
                    void save({ iq_ask_model: askModel.trim() });
                  }
                }}
                placeholder={current.llm_model}
                spellCheck={false}
                className="mt-0.5 w-full px-2 py-1 rounded border border-border bg-surface text-sm font-mono text-text"
              />
            </label>
          </div>

          {error && (
            <div className="text-[11px] rounded border border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400 px-3 py-2">
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
