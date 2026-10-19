import { useCallback, useEffect, useId, useState } from "react";
import { Check, FlaskConical, RefreshCw, X } from "lucide-react";
import { api } from "../lib/api";
import { providerSetupIssue } from "../lib/modelPresetHealth";
import type {
  IQEmbeddingTestResponse,
  IQStatus,
  LLMModel,
  LLMProviderSpec,
  Settings,
} from "../lib/types";
import { Select, type SelectOption } from "./Select";
import { ProviderConfigurationBadge } from "./ProviderConfigurationBadge";

interface Props {
  settings: Settings | null;
  onSettings: (next: Settings) => void;
  providers: LLMProviderSpec[];
  /** Jumps to another Settings category (Model, MCP servers). */
  onNavigate: (category: string) => void;
}

const BACKEND_LABEL: Record<string, string> = {
  fts5: "SQLite FTS5 (BM25)",
  tsvector: "Postgres full-text (tsvector)",
  like: "substring scan (no full-text index)",
};

// Offered when the provider can't list embedding deployments itself.
const COMMON_EMBEDDING_MODELS = [
  "text-embedding-3-small",
  "text-embedding-3-large",
  "text-embedding-ada-002",
];

const DEFAULT_PROVIDER = "";

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/**
 * Settings → Precursor IQ: index health, a manual re-index, the opt-in
 * embeddings (provider, model, connection test) and the model answering Ask.
 * Providers are the ones configured under Settings → Model, so an Azure AI
 * Foundry endpoint is set up once and reused here. Like the OpenAI endpoint
 * card, changes apply at once rather than on the panel's Save — they drive
 * background work the user wants to see start.
 */
export function IQSettings({ settings, onSettings, providers, onNavigate }: Props) {
  const modelListId = useId();
  const [status, setStatus] = useState<IQStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState(settings?.iq_embedding_model ?? "");
  const [embedCatalog, setEmbedCatalog] = useState<LLMModel[]>([]);
  const [askCatalog, setAskCatalog] = useState<LLMModel[]>([]);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<IQEmbeddingTestResponse | null>(null);

  const defaultProvider = settings?.llm_provider ?? "";
  const embedProvider = settings?.iq_embedding_provider ?? DEFAULT_PROVIDER;
  const askProvider = settings?.iq_ask_provider ?? DEFAULT_PROVIDER;
  const askModel = settings?.iq_ask_model ?? "";
  const effectiveEmbedProvider = embedProvider || defaultProvider;
  const effectiveAskProvider = askProvider || defaultProvider;

  useEffect(() => {
    setModel(settings?.iq_embedding_model ?? "");
  }, [settings?.iq_embedding_model]);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.iq.status());
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Catalogues of the providers actually in play, for the two model pickers.
  useEffect(() => {
    if (!effectiveEmbedProvider) return;
    let cancelled = false;
    api.llm
      .listModels(effectiveEmbedProvider)
      .then((m) => !cancelled && setEmbedCatalog(m))
      .catch(() => !cancelled && setEmbedCatalog([]));
    return () => {
      cancelled = true;
    };
  }, [effectiveEmbedProvider]);
  useEffect(() => {
    if (!effectiveAskProvider) return;
    let cancelled = false;
    api.llm
      .listModels(effectiveAskProvider)
      .then((m) => !cancelled && setAskCatalog(m))
      .catch(() => !cancelled && setAskCatalog([]));
    return () => {
      cancelled = true;
    };
  }, [effectiveAskProvider]);

  // Poll while there's indexing work in flight so the counters move.
  const embeddingBacklog =
    !!status?.embeddings_enabled &&
    !!status.embeddings_available &&
    status.embedded < status.chunks;
  const working = !!status && (status.pending > 0 || embeddingBacklog);
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
      setTest(null);
      await refresh();
    } catch (e) {
      setError(errorText(e));
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
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function runTest(): Promise<void> {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.iq.testEmbeddings(embedProvider, model.trim() || undefined));
    } catch (e) {
      setTest({
        ok: false,
        provider: effectiveEmbedProvider,
        model: model.trim(),
        dimensions: null,
        latency_ms: null,
        detail: errorText(e),
      });
    } finally {
      setTesting(false);
    }
  }

  const providerOptions = (defaultLabel: string): SelectOption[] => [
    { value: DEFAULT_PROVIDER, label: defaultLabel },
    ...providers.map((p) => ({
      value: p.id,
      label: p.label,
      suffix: <ProviderConfigurationBadge settings={current} provider={p} />,
    })),
  ];
  const providerLabel = (id: string) =>
    providers.find((p) => p.id === id)?.label ?? id;
  const embedSetupIssue = providerSetupIssue(
    current,
    providers.find((p) => p.id === effectiveEmbedProvider),
  );
  const askSetupIssue = providerSetupIssue(
    current,
    providers.find((p) => p.id === effectiveAskProvider),
  );

  // Keep a saved model selectable even when it is missing from the catalogue.
  const askModelOptions: SelectOption[] = [
    { value: "", label: "Chat model (default)" },
    ...(askModel && !askCatalog.some((m) => m.id === askModel)
      ? [{ value: askModel, label: askModel }]
      : []),
    ...askCatalog.map((m) => ({ value: m.id, label: m.name || m.id })),
  ];
  const embedSuggestions = [
    ...new Set([
      ...embedCatalog.map((m) => m.id),
      ...(effectiveEmbedProvider === "azure_foundry" ? [] : COMMON_EMBEDDING_MODELS),
    ]),
  ];

  const summary = !status
    ? "…"
    : !status.enabled
      ? "disabled"
      : status.pending > 0
        ? `indexing · ${status.pending} queued`
        : `${status.chunks.toLocaleString()} passages`;

  return (
    <section className="space-y-5">
      <p className="text-[11px] text-muted">
        Indexes your topics, briefs, messages, attachments, chats, agent runs,
        live notes and memory so the ⌘K palette ranks by relevance and can{" "}
        <span className="font-medium">Ask</span> for a cited answer. Offer{" "}
        <span className="font-mono">IQ retrieve</span> /{" "}
        <span className="font-mono">IQ ask</span> to MCP hosts from{" "}
        <button
          type="button"
          onClick={() => onNavigate("mcp")}
          className="underline hover:text-text"
        >
          MCP servers
        </button>
        .
      </p>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium flex-1">Index</h3>
          <span className="text-[11px] text-muted">{summary}</span>
        </div>
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
      </div>

      <div className="space-y-3 border-t border-border pt-4">
        <h3 className="text-sm font-medium">Semantic matching (embeddings)</h3>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={current.iq_embeddings_enabled}
            disabled={busy}
            onChange={(e) => void save({ iq_embeddings_enabled: e.target.checked })}
            className="mt-0.5 accent-accent"
          />
          <span className="min-w-0">
            <span className="block text-sm">Match paraphrases, not just shared words</span>
            <span className="block text-[11px] text-muted">
              Every indexed passage is sent once to the provider below, which
              uses its quota. Changing the provider or model re-embeds
              everything in the background.
            </span>
          </span>
        </label>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="min-w-0">
            <div className="block text-[11px] text-muted mb-1">Embeddings provider</div>
            <Select
              value={embedProvider}
              onChange={(next) => void save({ iq_embedding_provider: next })}
              options={providerOptions(
                `Default provider (${providerLabel(defaultProvider)})`,
              )}
              disabled={busy}
              ariaLabel="Embeddings provider"
              fullWidth
            />
          </div>
          <div className="min-w-0">
            <label className="block text-[11px] text-muted mb-1" htmlFor={`${modelListId}-in`}>
              {effectiveEmbedProvider === "azure_foundry"
                ? "Embeddings deployment"
                : "Embeddings model"}
            </label>
            <input
              id={`${modelListId}-in`}
              type="text"
              list={modelListId}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              onBlur={() => {
                if (model.trim() && model.trim() !== current.iq_embedding_model) {
                  void save({ iq_embedding_model: model.trim() });
                }
              }}
              placeholder="text-embedding-3-small"
              spellCheck={false}
              className="w-full px-2 py-1.5 rounded border border-border bg-surface text-sm font-mono text-text"
            />
            <datalist id={modelListId}>
              {embedSuggestions.map((id) => (
                <option key={id} value={id} />
              ))}
            </datalist>
          </div>
        </div>

        {embedSetupIssue && (
          <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
            {embedSetupIssue}{" "}
            <button
              type="button"
              onClick={() => onNavigate("model")}
              className="underline"
            >
              Open Model settings
            </button>
          </div>
        )}
        {current.iq_embeddings_enabled &&
          status &&
          !status.embeddings_available &&
          !embedSetupIssue && (
            <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
              {providerLabel(status.embedding_provider || effectiveEmbedProvider)} has
              no usable embeddings endpoint; retrieval stays full-text only.
            </div>
          )}
        <p className="text-[11px] text-muted">
          Azure AI Foundry reuses the endpoint, key and API version from{" "}
          <button
            type="button"
            onClick={() => onNavigate("model")}
            className="underline hover:text-text"
          >
            Settings → Model
          </button>
          ; add the embeddings deployment to its deployment names to pick it
          from the list.
        </p>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => void runTest()}
            disabled={testing || busy}
            className="flex items-center gap-1.5 rounded border border-border px-2 py-1 text-xs hover:bg-surface disabled:opacity-50"
          >
            <FlaskConical size={12} className={testing ? "animate-pulse" : ""} />
            {testing ? "Testing…" : "Test embeddings"}
          </button>
          {test && (
            <span
              className={`flex items-center gap-1 text-[11px] ${
                test.ok ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"
              }`}
              role="status"
            >
              {test.ok ? <Check size={12} /> : <X size={12} />}
              {test.ok
                ? `${providerLabel(test.provider)} · ${test.model} · ${test.dimensions} dimensions · ${test.latency_ms} ms`
                : test.detail || "Test failed"}
            </span>
          )}
        </div>
      </div>

      <div className="space-y-3 border-t border-border pt-4">
        <h3 className="text-sm font-medium">Answers (Ask)</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="min-w-0">
            <div className="block text-[11px] text-muted mb-1">Answer provider</div>
            <Select
              value={askProvider}
              onChange={(next) => void save({ iq_ask_provider: next, iq_ask_model: "" })}
              options={providerOptions(
                `Default provider (${providerLabel(defaultProvider)})`,
              )}
              disabled={busy}
              ariaLabel="Answer provider"
              fullWidth
            />
          </div>
          <div className="min-w-0">
            <div className="block text-[11px] text-muted mb-1">Answer model</div>
            <Select
              value={askModel}
              onChange={(next) => void save({ iq_ask_model: next })}
              options={askModelOptions}
              disabled={busy}
              ariaLabel="Answer model"
              searchable
              fullWidth
            />
          </div>
        </div>
        <p className="text-[11px] text-muted">
          {askModel
            ? "Cited answers are written by this model."
            : "Cited answers use your chat model; pick a model to use another provider."}
        </p>
        {askModel && askSetupIssue && (
          <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
            {askSetupIssue}
          </div>
        )}
      </div>

      {error && (
        <div className="text-[11px] rounded border border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400 px-3 py-2">
          {error}
        </div>
      )}
    </section>
  );
}
