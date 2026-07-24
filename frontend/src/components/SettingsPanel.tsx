import { useEffect, useState } from "react";
import {
  X,
  ChevronDown,
  ChevronRight,
  Palette,
  MessageSquare,
  Cpu,
  Plug,
  Radio,
  Plus,
  Pencil,
  Trash2,
  Sparkles,
  Drama,
  Brain,
  SlidersHorizontal,
  Mic,
  BarChart3,
  Bot,
  LogIn,
  RefreshCw,
  HardDriveDownload,
} from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { api } from "../lib/api";
import { mcpAuthStore } from "../lib/mcpAuth";
import { signInWorkiq } from "../lib/workiqSignIn";
import { setTheme, getStoredTheme, type Theme } from "../lib/theme";
import { modelsStore } from "../lib/modelsStore";
import { settingsStore } from "../lib/settingsStore";
import {
  notificationsSupported,
  requestNotificationPermission,
} from "../lib/notifications";
import type {
  BackupRunResult,
  LLMModel,
  LLMProviderSpec,
  MCPServerStatus,
  MCPServerCreate,
  MCPServerUpdate,
  Me,
  Settings,
} from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import { Select } from "./Select";
import { SidebarTabs } from "./SidebarTabs";
import { SkillsTab } from "./SkillsTab";
import { RolesTab } from "./RolesTab";
import { MemoriesTab } from "./MemoriesTab";
import { StatsTab } from "./StatsTab";
import { AgentsSettings } from "./AgentsSettings";

interface Props {
  onClose: () => void;
}

// The editable "System" settings subset of the full Settings payload.
interface SystemSettings {
  llm_max_input_tokens: number;
  llm_max_tool_result_tokens: number;
  scheduled_run_timeout_seconds: number;
  tool_result_retention_days: number;
  cmd_runner_jail: boolean;
  cmd_runner_image: string;
  cmd_runner_network: boolean;
  cmd_runner_timeout_seconds: number;
  cmd_runner_max_output_bytes: number;
  cmd_runner_memory: string;
  cmd_runner_pids_limit: number;
  cmd_runner_cpus: string;
}

function pickSystem(s: Settings): SystemSettings {
  return {
    llm_max_input_tokens: s.llm_max_input_tokens,
    llm_max_tool_result_tokens: s.llm_max_tool_result_tokens,
    scheduled_run_timeout_seconds: s.scheduled_run_timeout_seconds,
    tool_result_retention_days: s.tool_result_retention_days,
    cmd_runner_jail: s.cmd_runner_jail,
    cmd_runner_image: s.cmd_runner_image,
    cmd_runner_network: s.cmd_runner_network,
    cmd_runner_timeout_seconds: s.cmd_runner_timeout_seconds,
    cmd_runner_max_output_bytes: s.cmd_runner_max_output_bytes,
    cmd_runner_memory: s.cmd_runner_memory,
    cmd_runner_pids_limit: s.cmd_runner_pids_limit,
    cmd_runner_cpus: s.cmd_runner_cpus,
  };
}

type Category =
  | "appearance"
  | "chat"
  | "model"
  | "github"
  | "speech"
  | "live"
  | "mcp"
  | "skills"
  | "roles"
  | "memory"
  | "agents"
  | "stats"
  | "backup"
  | "system";

const CATEGORIES: ReadonlyArray<{
  id: Category;
  label: string;
  icon: typeof Palette;
  group: "App" | "Integrations" | "Extensions" | "Advanced";
}> = [
  { id: "appearance", label: "Appearance", icon: Palette, group: "App" },
  { id: "chat", label: "Chat", icon: MessageSquare, group: "App" },
  { id: "model", label: "Model", icon: Cpu, group: "App" },
  { id: "github", label: "GitHub", icon: Github, group: "Integrations" },
  { id: "speech", label: "Speech-to-text", icon: Mic, group: "Integrations" },
  { id: "live", label: "Live", icon: Radio, group: "Integrations" },
  { id: "mcp", label: "MCP servers", icon: Plug, group: "Integrations" },
  { id: "skills", label: "Skills", icon: Sparkles, group: "Extensions" },
  { id: "roles", label: "Roles", icon: Drama, group: "Extensions" },
  { id: "memory", label: "Memory", icon: Brain, group: "Extensions" },
  { id: "agents", label: "Agents", icon: Bot, group: "Extensions" },
  { id: "stats", label: "Usage stats", icon: BarChart3, group: "Advanced" },
  { id: "backup", label: "Backup", icon: HardDriveDownload, group: "Advanced" },
  { id: "system", label: "System", icon: SlidersHorizontal, group: "Advanced" },
];

// Speech-to-text recognition languages (BCP-47). "" => use the browser locale.
const STT_LANGUAGES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "Auto (browser)" },
  { value: "en-US", label: "English (US)" },
  { value: "en-GB", label: "English (UK)" },
  { value: "fr-FR", label: "French (France)" },
  { value: "de-DE", label: "German" },
  { value: "es-ES", label: "Spanish (Spain)" },
  { value: "it-IT", label: "Italian" },
  { value: "pt-PT", label: "Portuguese (Portugal)" },
  { value: "pt-BR", label: "Portuguese (Brazil)" },
  { value: "nl-NL", label: "Dutch" },
  { value: "ja-JP", label: "Japanese" },
  { value: "ko-KR", label: "Korean" },
  { value: "zh-CN", label: "Chinese (Mandarin, Simplified)" },
];

export function SettingsPanel({ onClose }: Props) {
  const confirmAction = useConfirm();
  const [category, setCategory] = useState<Category>("appearance");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [mcp, setMcp] = useState<MCPServerStatus[]>([]);
  const [mcpLoading, setMcpLoading] = useState(true);
  const [theme, setThemeState] = useState<Theme>(getStoredTheme());
  const [models, setModels] = useState<LLMModel[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [provider, setProvider] = useState("");
  const [providers, setProviders] = useState<LLMProviderSpec[]>([]);
  const [providerConfig, setProviderConfig] = useState<
    Record<string, Record<string, string>>
  >({});
  const [repo, setRepo] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [azureEndpoint, setAzureEndpoint] = useState("");
  const [azureLanguage, setAzureLanguage] = useState("");
  const [azureKey, setAzureKey] = useState("");
  const [liveFastModel, setLiveFastModel] = useState("");
  const [liveReasoningEffort, setLiveReasoningEffort] = useState("");
  const [liveEnabled, setLiveEnabled] = useState(true);
  const [liveTranscriptRetentionDays, setLiveTranscriptRetentionDays] = useState(7);
  const [sttTest, setSttTest] = useState<
    { state: "idle" | "testing" | "ok" | "error"; detail?: string }
  >({ state: "idle" });
  const [ttlMinutes, setTtlMinutes] = useState(60);
  const [showChatStats, setShowChatStats] = useState(true);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [maxToolRounds, setMaxToolRounds] = useState(15);
  const [issueAssociationsEnabled, setIssueAssociationsEnabled] = useState(true);
  // Folder backup (DB + blobs) — editable prefs plus a manual "run now" action.
  const [backupEnabled, setBackupEnabled] = useState(false);
  const [backupDir, setBackupDir] = useState("");
  const [backupRetention, setBackupRetention] = useState(7);
  const [backupRunning, setBackupRunning] = useState(false);
  const [backupRunResult, setBackupRunResult] = useState<BackupRunResult | null>(null);
  // System settings (env default + DB override). Loaded as a single object.
  const [sys, setSys] = useState<SystemSettings | null>(null);
  const [dockerAvailable, setDockerAvailable] = useState(false);
  // Per-section exposure of Precursor's own capabilities over the built-in
  // "precursor" MCP server.
  const [expose, setExpose] = useState<Record<string, boolean>>({});
  // HTTP (localhost) transport for the built-in "precursor" MCP server.
  const [httpEnabled, setHttpEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [mcpEditing, setMcpEditing] = useState<MCPServerStatus | "new" | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [appVersion, setAppVersion] = useState<string | null>(null);

  async function refreshMcp(): Promise<void> {
    setMcpLoading(true);
    let servers: MCPServerStatus[];
    try {
      // Load the list fast, without waiting on per-server status probes so the
      // cards render immediately instead of blocking on a slow server.
      servers = await api.mcp.list(false);
      setMcp(servers);
    } catch {
      setMcp([]);
      return;
    } finally {
      setMcpLoading(false);
    }
    // Resolve each pending server's status independently: a slow server only
    // spins its own card rather than stalling the whole list.
    const pending = servers.filter((s) => s.enabled && s.state === "connecting");
    await Promise.all(
      pending.map(async (s) => {
        try {
          const next = await api.mcp.probe(s.name);
          setMcp((prev) => prev.map((x) => (x.name === s.name ? next : x)));
        } catch (err) {
          setMcp((prev) =>
            prev.map((x) =>
              x.name === s.name
                ? { ...x, state: "error", error: (err as Error).message }
                : x,
            ),
          );
        }
      }),
    );
  }

  async function loadModels(providerOverride?: string): Promise<LLMModel[]> {
    setModelsLoading(true);
    setModelsError(null);
    try {
      const list = await api.llm.listModels(providerOverride);
      setModels(list);
      return list;
    } catch (e) {
      setModels([]);
      setModelsError(e instanceof Error ? e.message : String(e));
      return [];
    } finally {
      setModelsLoading(false);
    }
  }

  // Switching providers reloads the catalog for the newly selected provider
  // (previewed via the override param, using its saved config) so the model
  // list and selection always match the chosen provider.
  function onProviderChange(next: string): void {
    setProvider(next);
    void loadModels(next);
  }

  useEffect(() => {
    void (async () => {
      const s = await api.settings.get();
      setSettings(s);
      setProvider(s.llm_provider);
      setProviderConfig(s.llm_providers ?? {});
      setRepo(s.github_repo);
      setTtlMinutes(s.issue_context_ttl_minutes);
      setShowChatStats(s.show_chat_stats);
      setNotificationsEnabled(s.notifications_enabled);
      setMaxToolRounds(s.max_tool_rounds);
      setIssueAssociationsEnabled(s.issue_associations_enabled);
      setBackupEnabled(s.backup_enabled);
      setBackupDir(s.backup_dir);
      setBackupRetention(s.backup_retention);
      setAzureEndpoint(s.azure_speech_endpoint);
      setAzureLanguage(s.azure_speech_language);
      setLiveFastModel(s.live_fast_model);
      setLiveReasoningEffort(s.live_reasoning_effort);
      setLiveEnabled(s.live_enabled);
      setLiveTranscriptRetentionDays(s.live_transcript_retention_days);
      setSys(pickSystem(s));
      setDockerAvailable(s.docker_available);
      setExpose(s.mcp_expose ?? {});
      setHttpEnabled(s.mcp_http_enabled);
      settingsStore.set(s);
      await refreshMcp();
      try {
        setProviders(await api.llm.listProviders());
      } catch {
        setProviders([]);
      }
      await loadModels();
      try {
        setMe(await api.me.get());
      } catch {
        setMe({ github: null, github_token_source: "none" });
      }
      try {
        setAppVersion((await api.system.getVersion()).version);
      } catch {
        setAppVersion(null);
      }
    })();
  }, []);

  // Build the llm_providers payload from edits, omitting empty secret fields so
  // a blank password input never clears an already-saved secret.
  function buildLlmProvidersPayload(): Record<string, Record<string, string>> {
    const secretFields: Record<string, Set<string>> = {};
    for (const spec of providers) {
      secretFields[spec.id] = new Set(
        spec.fields.filter((f) => f.secret).map((f) => f.name),
      );
    }
    const out: Record<string, Record<string, string>> = {};
    for (const [pid, cfg] of Object.entries(providerConfig)) {
      const entry: Record<string, string> = {};
      for (const [k, v] of Object.entries(cfg)) {
        if (secretFields[pid]?.has(k) && v === "") continue;
        entry[k] = v;
      }
      if (Object.keys(entry).length > 0) out[pid] = entry;
    }
    return out;
  }

  // Persist the provider + its config, then refresh the catalog — without
  // closing the panel (so the user can verify discovery). Model and reasoning
  // effort are chosen in the composer now, not here; but if the previously
  // selected global model isn't in the new provider's catalog we snap it to a
  // valid one so the composer never points at a stale id.
  async function applyProviderSettings(): Promise<void> {
    setModelsLoading(true);
    try {
      const updated = await api.settings.update({
        llm_provider: provider,
        llm_providers: buildLlmProvidersPayload(),
      });
      setSettings(updated);
      setProviderConfig(updated.llm_providers ?? {});
      settingsStore.set(updated);
      const list = await loadModels(provider);
      if (list.length > 0 && !list.some((m) => m.id === updated.llm_model)) {
        const snapped = await api.settings.update({ llm_model: list[0].id });
        setSettings(snapped);
        settingsStore.set(snapped);
        modelsStore.applySettings(snapped);
      } else {
        modelsStore.applySettings(updated);
      }
    } catch (e) {
      setModelsError(e instanceof Error ? e.message : String(e));
      setModelsLoading(false);
    }
  }

  async function save(): Promise<void> {
    setSaving(true);
    try {
      const payload: Parameters<typeof api.settings.update>[0] = {
        theme,
        llm_provider: provider,
        github_repo: repo,
        issue_context_ttl_minutes: ttlMinutes,
        show_chat_stats: showChatStats,
        notifications_enabled: notificationsEnabled,
        max_tool_rounds: maxToolRounds,
        issue_associations_enabled: issueAssociationsEnabled,
        azure_speech_endpoint: azureEndpoint,
        azure_speech_language: azureLanguage,
        live_fast_model: liveFastModel,
        live_reasoning_effort: liveReasoningEffort,
        live_enabled: liveEnabled,
        live_transcript_retention_days: liveTranscriptRetentionDays,
        mcp_expose: expose,
        mcp_http_enabled: httpEnabled,
        backup_enabled: backupEnabled,
        backup_dir: backupDir,
        backup_retention: backupRetention,
        ...(sys ?? {}),
      };
      const llmProviders = buildLlmProvidersPayload();
      if (Object.keys(llmProviders).length > 0) {
        payload.llm_providers = llmProviders;
      }
      const apiKeys: Record<string, string> = {};
      if (githubToken) apiKeys.github_token = githubToken;
      if (azureKey) apiKeys.azure_speech_key = azureKey;
      if (Object.keys(apiKeys).length > 0) {
        payload.api_keys = apiKeys;
      }
      const updated = await api.settings.update(payload);
      setSettings(updated);
      modelsStore.applySettings(updated);
      settingsStore.set(updated);
      setGithubToken("");
      setAzureKey("");
      setTheme(theme);
      onClose();
    } finally {
      setSaving(false);
    }
  }

  // Persist the current backup prefs, then run one immediately so the button
  // acts on exactly what the user sees (not a stale saved value). Refreshes the
  // settings snapshot afterwards so the last-run status updates in place.
  async function runBackupNow(): Promise<void> {
    setBackupRunning(true);
    setBackupRunResult(null);
    try {
      const updated = await api.settings.update({
        backup_enabled: backupEnabled,
        backup_dir: backupDir,
        backup_retention: backupRetention,
      });
      setSettings(updated);
      settingsStore.set(updated);
      const result = await api.settings.runBackupNow();
      setBackupRunResult(result);
      // Pull the fresh last-run status into the snapshot the tab reads.
      const refreshed = await api.settings.get();
      setSettings(refreshed);
      settingsStore.set(refreshed);
    } catch (e) {
      setBackupRunResult({
        ok: false,
        status: "error",
        detail: e instanceof Error ? e.message : String(e),
        db_snapshot: null,
        blobs_copied: 0,
        blobs_total: 0,
      });
    } finally {
      setBackupRunning(false);
    }
  }

  async function testStt(): Promise<void> {
    setSttTest({ state: "testing" });
    try {
      const res = await api.stt.testConnection(azureEndpoint, azureKey);
      setSttTest({
        state: res.ok ? "ok" : "error",
        detail: res.detail ?? undefined,
      });
    } catch (e) {
      setSttTest({
        state: "error",
        detail: e instanceof Error ? e.message : "Test failed",
      });
    }
  }

  async function toggleMcp(name: string, enabled: boolean): Promise<void> {
    // Optimistic update so the switch animates immediately.
    setMcp((prev) =>
      prev.map((s) =>
        s.name === name
          ? { ...s, enabled: !enabled, state: !enabled ? "connecting" : "disabled" }
          : s,
      ),
    );
    try {
      const next = enabled
        ? await api.mcp.disconnect(name)
        : await api.mcp.connect(name);
      setMcp((prev) => prev.map((s) => (s.name === name ? next : s)));
    } catch (err) {
      setMcp((prev) =>
        prev.map((s) =>
          s.name === name
            ? { ...s, enabled, state: "error", error: (err as Error).message }
            : s,
        ),
      );
    }
  }

  async function togglePreview(name: string, current: boolean): Promise<void> {
    const nextValue = !current;
    // Optimistic flip; the round-trip may trigger an OAuth browser sign-in.
    setMcp((prev) =>
      prev.map((s) => (s.name === name ? { ...s, preview: nextValue } : s)),
    );
    try {
      const next = await api.mcp.setWorkiqPreview(nextValue);
      setMcp((prev) => prev.map((s) => (s.name === name ? next : s)));
    } catch (err) {
      setMcp((prev) =>
        prev.map((s) =>
          s.name === name
            ? { ...s, preview: current, state: "error", error: (err as Error).message }
            : s,
        ),
      );
    }
  }

  async function reloadMcpTools(name: string): Promise<void> {
    // Re-probe an enabled server to refresh its tool catalogue in place.
    setMcp((prev) =>
      prev.map((s) =>
        s.name === name ? { ...s, state: "connecting", error: null } : s,
      ),
    );
    try {
      const next = await api.mcp.refresh(name);
      setMcp((prev) => prev.map((s) => (s.name === name ? next : s)));
    } catch (err) {
      setMcp((prev) =>
        prev.map((s) =>
          s.name === name
            ? { ...s, state: "error", error: (err as Error).message }
            : s,
        ),
      );
    }
  }

  async function reauthenticateWorkiq(name: string): Promise<void> {
    // Opens a script-openable sign-in popup and blocks until the user finishes.
    setMcp((prev) =>
      prev.map((s) =>
        s.name === name ? { ...s, state: "connecting", error: null } : s,
      ),
    );
    try {
      const next = await signInWorkiq();
      if (!next) {
        // The user abandoned the sign-in (closed the popup) — restore the
        // needs-auth state without an error.
        setMcp((prev) =>
          prev.map((s) => (s.name === name ? { ...s, state: "needs_auth" } : s)),
        );
        return;
      }
      setMcp((prev) => prev.map((s) => (s.name === name ? next : s)));
      mcpAuthStore.clear();
    } catch (err) {
      setMcp((prev) =>
        prev.map((s) =>
          s.name === name
            ? { ...s, state: "needs_auth", error: (err as Error).message }
            : s,
        ),
      );
    }
  }

  // Esc closes the modal — convention shared with most settings dialogs
  // (VS Code, Linear, GitHub).
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="bg-bg border border-border rounded-lg shadow-2xl flex flex-col w-full overflow-hidden"
        style={{ maxWidth: 960, height: "min(720px, 90vh)" }}
      >
        <header className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0">
          <h2 className="font-semibold">Settings</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close (Esc)"
          >
            <X size={18} />
          </button>
        </header>

        <div className="flex flex-1 min-h-0">
          <SidebarTabs
            groups={["App", "Integrations", "Extensions", "Advanced"] as const}
            tabs={CATEGORIES}
            active={category}
            onSelect={setCategory}
          />

          <div className="flex-1 overflow-y-auto p-6 space-y-6 min-w-0">
            {category === "appearance" && (
              <section>
                <h3 className="text-sm font-medium mb-2">Theme</h3>
                <div className="flex gap-2">
                  {(["light", "dark", "system"] as const).map((t) => (
                    <button
                      key={t}
                      onClick={() => setThemeState(t)}
                      className={`px-3 py-1.5 rounded border text-sm capitalize ${
                        theme === t ? "border-accent text-accent" : "border-border text-text"
                      }`}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </section>
            )}

            {category === "chat" && (
              <>
                <section>
                  <h3 className="text-sm font-medium mb-2">Interface</h3>
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={showChatStats}
                      onChange={(e) => setShowChatStats(e.target.checked)}
                      className="mt-0.5 accent-accent"
                    />
                    <span>
                      <span className="block text-sm">
                        Show conversation stats sidebar
                      </span>
                      <span className="block text-[11px] text-muted">
                        Displays token usage and context-window occupancy next
                        to each chat. Stats are always collected; this only
                        controls the panel's visibility.
                      </span>
                    </span>
                  </label>
                </section>

                <section>
                  <h3 className="text-sm font-medium mb-2">Notifications</h3>
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={notificationsEnabled}
                      onChange={async (e) => {
                        const on = e.target.checked;
                        if (on) {
                          const perm = await requestNotificationPermission();
                          if (perm !== "granted") {
                            setNotificationsEnabled(false);
                            return;
                          }
                        }
                        setNotificationsEnabled(on);
                      }}
                      disabled={!notificationsSupported()}
                      className="mt-0.5 accent-accent"
                    />
                    <span>
                      <span className="block text-sm">
                        Notify when a reply is ready
                      </span>
                      <span className="block text-[11px] text-muted">
                        {notificationsSupported()
                          ? "Shows a browser notification when an assistant turn (including scheduled tasks) finishes while the Precursor window isn't focused. The unread count always appears in the tab title regardless of this setting."
                          : "Your browser doesn't support notifications."}
                      </span>
                    </span>
                  </label>
                </section>

                <section>
                  <h3 className="text-sm font-medium mb-2">Tools</h3>
                  <label className="block text-xs text-muted mb-1">
                    Max tool-call rounds per message
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    value={maxToolRounds}
                    onChange={(e) =>
                      setMaxToolRounds(
                        Math.max(1, Math.min(1000, Number(e.target.value) || 1)),
                      )
                    }
                    className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                  />
                  <p className="text-[11px] text-muted mt-1">
                    Hard ceiling on the number of tool-call iterations the
                    assistant may chain before the stream aborts. Increase for
                    multi-step agents (e.g. browser automation); decrease to
                    cut runaway loops short. Range: 1–1000.
                  </p>
                </section>
              </>
            )}

            {category === "model" && (
              <section>
                <h3 className="text-sm font-medium mb-2">Provider</h3>
                <Select
                  value={provider}
                  onChange={onProviderChange}
                  options={providers.map((p) => ({ value: p.id, label: p.label }))}
                  ariaLabel="LLM provider"
                  fullWidth
                />

                {(() => {
                  const activeSpec = providers.find((p) => p.id === provider);
                  if (!activeSpec) return null;
                  if (activeSpec.uses_github_token) {
                    return (
                      <p className="text-[11px] text-muted mt-2">
                        Authenticates with your GitHub token (configured in
                        Settings → GitHub, or your <code>gh auth login</code>{" "}
                        session).
                      </p>
                    );
                  }
                  if (activeSpec.fields.length === 0) return null;
                  const cfg = providerConfig[provider] ?? {};
                  const present =
                    settings?.llm_providers_present?.[provider] ?? {};
                  return (
                    <div className="mt-3 space-y-3 p-3 rounded border border-border bg-surface">
                      {activeSpec.fields.map((f) => (
                        <div key={f.name}>
                          <label className="block text-xs text-muted mb-1">
                            {f.label}
                            {f.secret && present[f.name] && (
                              <span className="text-green-500"> (configured)</span>
                            )}
                          </label>
                          <input
                            type={f.secret ? "password" : "text"}
                            value={cfg[f.name] ?? ""}
                            onChange={(e) =>
                              setProviderConfig((prev) => ({
                                ...prev,
                                [provider]: {
                                  ...(prev[provider] ?? {}),
                                  [f.name]: e.target.value,
                                },
                              }))
                            }
                            placeholder={
                              f.secret && present[f.name]
                                ? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
                                : f.placeholder
                            }
                            className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                          />
                          {f.help && (
                            <p className="text-[11px] text-muted mt-1">{f.help}</p>
                          )}
                        </div>
                      ))}
                      <p className="text-[11px] text-muted">
                        Secrets are stored server-side and never returned. Leave a
                        configured key blank to keep it.
                      </p>
                    </div>
                  );
                })()}

                <div className="flex items-center gap-2 mt-4">
                  <button
                    type="button"
                    onClick={() => void applyProviderSettings()}
                    disabled={modelsLoading}
                    className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50"
                  >
                    {modelsLoading ? "Refreshing\u2026" : "Apply & refresh models"}
                  </button>
                  <span className="text-[11px] text-muted">
                    {modelsError
                      ? `Catalog unavailable: ${modelsError}.`
                      : models.length > 0
                        ? `${models.length} models`
                        : "No catalog for this provider."}
                  </span>
                </div>
                <p className="text-[11px] text-muted mt-2">
                  Pick the model, reasoning effort and context size from the
                  composer toolbar — they apply to every conversation.
                </p>

                {sys && (
                  <div className="mt-6 space-y-3">
                    <h3 className="text-sm font-medium">Prompt budgeting</h3>
                    <p className="text-[11px] text-muted">
                      Caps how much of the transcript is sent to the model so a
                      few large reads / tool results can't overflow the context
                      window. Lower these for smaller models.
                    </p>
                    <div>
                      <label className="block text-xs text-muted mb-1">
                        Max input tokens per request
                      </label>
                      <NumberInput
                        value={sys.llm_max_input_tokens}
                        min={1000}
                        max={5_000_000}
                        onCommit={(n) =>
                          setSys({ ...sys, llm_max_input_tokens: n })
                        }
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-muted mb-1">
                        Max tokens per tool result
                      </label>
                      <NumberInput
                        value={sys.llm_max_tool_result_tokens}
                        min={100}
                        max={2_000_000}
                        onCommit={(n) =>
                          setSys({ ...sys, llm_max_tool_result_tokens: n })
                        }
                      />
                      <p className="text-[11px] text-muted mt-1">
                        Per-message ceiling applied to individual tool outputs
                        before they enter the transcript.
                      </p>
                    </div>
                  </div>
                )}
              </section>
            )}

            {category === "github" && (
              <section>
                <GitHubStatusBanner me={me} />

                <div className="mb-4 p-3 rounded border border-border bg-surface">
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={issueAssociationsEnabled}
                      onChange={(e) => setIssueAssociationsEnabled(e.target.checked)}
                      className="mt-0.5 accent-accent"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">
                        Enable GitHub issue associations
                      </span>
                      <span className="block text-[11px] text-muted mt-0.5">
                        When on, topics can be linked to GitHub issues, the
                        header shows issue status, and the{" "}
                        <code className="font-mono">/gh-update</code>,{" "}
                        <code className="font-mono">/gh-sync</code>,{" "}
                        <code className="font-mono">/gh-create</code>,{" "}
                        <code className="font-mono">/gh-close</code>{" "}
                        slash commands are available. When off, every issue
                        affordance is hidden across the app and related API
                        calls are rejected. Existing topic→issue links and
                        cached contexts are preserved — turn the feature back
                        on to keep where you left off.
                      </span>
                    </span>
                  </label>
                </div>

                {issueAssociationsEnabled && (
                  <fieldset>
                    <label className="block text-xs text-muted mb-1">
                      Reference repository
                    </label>
                    <input
                      type="text"
                      value={repo}
                      onChange={(e) => setRepo(e.target.value)}
                      placeholder="owner/name"
                      className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent mb-3"
                    />
                    <label className="block text-xs text-muted mt-3 mb-1">
                      Issue context cache (minutes)
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={10080}
                      value={ttlMinutes}
                      onChange={(e) => setTtlMinutes(Number(e.target.value) || 1)}
                      className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                    />
                    <p className="text-[11px] text-muted mt-1">
                      How long a topic's GitHub-issue summary is reused before being
                      automatically refreshed. Forcing a sync from the Context tab
                      always regenerates it.
                    </p>
                  </fieldset>
                )}

                <label className="block text-xs text-muted mt-4 mb-1">
                  Personal access token{" "}
                  {settings?.api_keys_present?.github_token && (
                    <span className="text-green-500">(configured)</span>
                  )}
                </label>
                <input
                  type="password"
                  value={githubToken}
                  onChange={(e) => setGithubToken(e.target.value)}
                  placeholder="ghp_..."
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                />
                {settings && (
                  <p className="text-[11px] text-muted mt-1">
                    {settings.github_token_source === "env" &&
                      "Using GITHUB_TOKEN from the environment."}
                    {settings.github_token_source === "gh-cli" &&
                      "No token configured — using your gh CLI login. Leave blank to keep using it."}
                    {settings.github_token_source === "settings" &&
                      "Using token saved in Settings."}
                    {settings.github_token_source === "none" &&
                      "No token detected. Provide a PAT here, set GITHUB_TOKEN, or run `gh auth login`."}
                  </p>
                )}
              </section>
            )}

            {category === "speech" && (
              <section>
                <p className="text-sm text-muted mb-3">
                  Dictate prompts with the mic button in the chat composer.
                  Configure an <strong>Azure AI Speech</strong> resource to
                  enable speech-to-text. Without it, the mic button is hidden.
                </p>
                <div
                  className={`mb-4 text-[11px] px-2 py-1.5 rounded border ${
                    settings?.stt_azure_ready
                      ? "border-green-600/40 text-green-500"
                      : "border-border text-muted"
                  }`}
                >
                  {settings?.stt_azure_ready
                    ? "Azure Speech is configured — dictation is enabled."
                    : "Azure Speech not configured — the mic button is hidden."}
                </div>

                <label className="block text-xs text-muted mb-1">Endpoint URL</label>
                <input
                  type="text"
                  value={azureEndpoint}
                  onChange={(e) => {
                    setAzureEndpoint(e.target.value);
                    setSttTest({ state: "idle" });
                  }}
                  placeholder="https://<name>.cognitiveservices.azure.com/"
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                />
                <p className="text-[11px] text-muted mt-1">
                  The resource endpoint URL from your Azure Speech / Cognitive
                  Services resource ("Keys and Endpoint").
                </p>

                <label className="block text-xs text-muted mt-4 mb-1">Language</label>
                <Select
                  value={azureLanguage}
                  onChange={setAzureLanguage}
                  options={STT_LANGUAGES}
                  ariaLabel="Speech recognition language"
                  fullWidth
                />
                <p className="text-[11px] text-muted mt-1">
                  Recognition language. "Auto (browser)" uses the browser's
                  current locale.
                </p>

                <label className="block text-xs text-muted mt-4 mb-1">
                  Subscription key{" "}
                  {settings?.api_keys_present?.azure_speech_key && (
                    <span className="text-green-500">(configured)</span>
                  )}
                </label>
                <input
                  type="password"
                  value={azureKey}
                  onChange={(e) => {
                    setAzureKey(e.target.value);
                    setSttTest({ state: "idle" });
                  }}
                  placeholder={
                    settings?.api_keys_present?.azure_speech_key
                      ? "••••••••••••••••"
                      : "Azure Speech key"
                  }
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                />
                <p className="text-[11px] text-muted mt-1">
                  Stored server-side and never returned. The browser only ever
                  receives a short-lived token minted from it. Leave blank to keep
                  the saved key.
                </p>

                <div className="flex items-center gap-2 mt-3">
                  <button
                    type="button"
                    onClick={() => void testStt()}
                    disabled={!azureEndpoint || sttTest.state === "testing"}
                    className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50"
                  >
                    {sttTest.state === "testing" ? "Testing…" : "Test connection"}
                  </button>
                  {sttTest.state === "ok" && (
                    <span className="text-[11px] text-green-500">
                      {sttTest.detail ?? "Connection OK."}
                    </span>
                  )}
                  {sttTest.state === "error" && (
                    <span className="text-[11px] text-red-500">
                      {sttTest.detail ?? "Test failed."}
                    </span>
                  )}
                </div>
              </section>
            )}

            {category === "live" && (
              <section>
                <p className="text-sm text-muted mb-3">
                  The <strong>Live</strong> section records a meeting, transcribes
                  it with speaker labels, surfaces live insights, and can attach a
                  summary to a topic. Transcription uses your{" "}
                  <strong>Azure Speech</strong> resource (configured under
                  Speech-to-text).
                </p>

                <label className="flex items-center gap-2 mb-4 text-sm">
                  <input
                    type="checkbox"
                    checked={liveEnabled}
                    onChange={(e) => setLiveEnabled(e.target.checked)}
                    className="accent-accent"
                  />
                  Enable the Live meeting assistant section
                </label>

                <div className="border-t border-border pt-4">
                  <h3 className="text-sm font-medium">Analysis model</h3>
                  <p className="text-[11px] text-muted mt-1 mb-3">
                    Model for the rolling live insights and Q&amp;A. A fast model
                    keeps analysis snappy; leave on the default to reuse your chat
                    model. (Summaries use the default chat model for quality.)
                  </p>

                  <label className="block text-xs text-muted mb-1">Fast model</label>
                  <Select
                    value={liveFastModel}
                    onChange={setLiveFastModel}
                    disabled={!liveEnabled}
                    ariaLabel="Live analysis fast model"
                    fullWidth
                    options={[
                      { value: "", label: "Use default chat model" },
                      ...models.map((m) => ({ value: m.id, label: m.name })),
                    ]}
                  />

                  <label className="block text-xs text-muted mt-4 mb-1">
                    Reasoning effort
                  </label>
                  <Select
                    value={liveReasoningEffort}
                    onChange={setLiveReasoningEffort}
                    disabled={!liveEnabled}
                    ariaLabel="Live analysis reasoning effort"
                    fullWidth
                    options={[
                      { value: "", label: "Auto / off (fastest)" },
                      { value: "low", label: "Low" },
                      { value: "medium", label: "Medium" },
                      { value: "high", label: "High" },
                    ]}
                  />
                  <p className="text-[11px] text-muted mt-1">
                    Live analysis favours speed — keep this low. Only applies to
                    reasoning-capable models.
                  </p>
                </div>

                <div className="border-t border-border pt-4 mt-4">
                  <h3 className="text-sm font-medium">Transcript retention</h3>
                  <p className="text-[11px] text-muted mt-1 mb-3">
                    Automatically delete a session's transcript this many days
                    after it ends, to keep the database small. Only the
                    transcript is removed — the session's insights, notes and
                    summary are kept. 0 = keep forever.
                  </p>
                  <label className="block text-xs text-muted mb-1">
                    Delete transcript after (days)
                  </label>
                  <NumberInput
                    value={liveTranscriptRetentionDays}
                    min={0}
                    max={3650}
                    onCommit={setLiveTranscriptRetentionDays}
                  />
                </div>
              </section>
            )}

            {category === "mcp" && (
              <section>
                <McpExposeCard
                  expose={expose}
                  setExpose={setExpose}
                  precursorEnabled={
                    mcp.find((s) => s.name === "precursor")?.enabled ?? false
                  }
                  httpEnabled={httpEnabled}
                  setHttpEnabled={setHttpEnabled}
                  httpUrl={settings?.mcp_http_url ?? null}
                  httpLoopbackOk={settings?.mcp_http_loopback_ok ?? true}
                />
                <div className="flex items-center justify-between mb-3">
                  <p className="text-[11px] text-muted">
                    Enable an MCP server to expose its tools to the chat. The
                    assistant will call them on its own when relevant.
                  </p>
                  <button
                    type="button"
                    onClick={() => setMcpEditing("new")}
                    className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs whitespace-nowrap"
                  >
                    <Plus size={12} /> New
                  </button>
                </div>
                {mcp.length === 0 ? (
                  mcpLoading ? (
                    <p className="flex items-center gap-2 text-xs text-muted">
                      <RefreshCw size={12} className="animate-spin" />
                      Loading MCP servers…
                    </p>
                  ) : (
                    <p className="text-xs text-muted">No MCP servers registered.</p>
                  )
                ) : (
                  <ul className="space-y-2">
                    {mcp.map((s) => (
                      <McpServerCard
                        key={s.name}
                        server={s}
                        onToggle={() => void toggleMcp(s.name, s.enabled)}
                        onReload={
                          s.enabled
                            ? () => void reloadMcpTools(s.name)
                            : undefined
                        }
                        onTogglePreview={
                          s.preview != null
                            ? () => void togglePreview(s.name, s.preview ?? false)
                            : undefined
                        }
                        onReauthenticate={
                          s.preview
                            ? () => void reauthenticateWorkiq(s.name)
                            : undefined
                        }
                        onEdit={() => setMcpEditing(s)}
                        onDelete={async () => {
                          if (s.id == null) return;
                          if (
                            !(await confirmAction({
                              message: `Delete MCP server "${s.name}"?`,
                              confirmLabel: "Delete MCP server",
                              variant: "danger",
                            }))
                          )
                            return;
                          await api.mcp.remove(s.id);
                          await refreshMcp();
                        }}
                      />
                    ))}
                  </ul>
                )}
                {mcpEditing && (
                  <McpServerEditor
                    server={mcpEditing === "new" ? null : mcpEditing}
                    onClose={() => setMcpEditing(null)}
                    onSaved={async () => {
                      await refreshMcp();
                      setMcpEditing(null);
                    }}
                  />
                )}
              </section>
            )}

            {category === "skills" && <SkillsTab />}

            {category === "roles" && <RolesTab />}

            {category === "memory" && <MemoriesTab />}
            {category === "agents" && <AgentsSettings />}

            {category === "stats" && <StatsTab />}

            {category === "backup" && (
              <BackupTab
                enabled={backupEnabled}
                setEnabled={setBackupEnabled}
                dir={backupDir}
                setDir={setBackupDir}
                retention={backupRetention}
                setRetention={setBackupRetention}
                lastRunAt={settings?.backup_last_run_at ?? null}
                lastStatus={settings?.backup_last_status ?? null}
                lastError={settings?.backup_last_error ?? null}
                running={backupRunning}
                runResult={backupRunResult}
                onRunNow={() => void runBackupNow()}
              />
            )}

            {category === "system" && sys && (
              <SystemTab
                sys={sys}
                setSys={setSys}
                dockerAvailable={dockerAvailable}
              />
            )}
          </div>
        </div>

        <footer className="border-t border-border p-3 flex items-center justify-end gap-2 shrink-0">
          <span
            className="mr-auto text-[11px] text-muted"
            data-tooltip="Precursor version"
          >
            {appVersion ? `Precursor v${appVersion}` : ""}
          </span>
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={saving}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function BackupTab({
  enabled,
  setEnabled,
  dir,
  setDir,
  retention,
  setRetention,
  lastRunAt,
  lastStatus,
  lastError,
  running,
  runResult,
  onRunNow,
}: {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
  dir: string;
  setDir: (v: string) => void;
  retention: number;
  setRetention: (v: number) => void;
  lastRunAt: string | null;
  lastStatus: string | null;
  lastError: string | null;
  running: boolean;
  runResult: BackupRunResult | null;
  onRunNow: () => void;
}) {
  const lastRunLabel = lastRunAt ? new Date(lastRunAt).toLocaleString() : "Never";
  return (
    <div className="space-y-6">
      <p className="text-[11px] text-muted">
        Copy the database and attachment files into a folder on a daily schedule.
        Point this at a cloud-synced directory (e.g. OneDrive, Dropbox, iCloud
        Drive) to keep an off-machine copy. The live database stays on local disk —
        only a consistent snapshot is written to the folder, which is safe to sync.
      </p>

      <section className="space-y-3">
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">Enable daily backup</span>
            <span className="block text-[11px] text-muted">
              Runs about once every 24 hours while the app is running.
            </span>
          </span>
        </label>

        <div>
          <label className="block text-xs text-muted mb-1">Backup folder</label>
          <input
            type="text"
            value={dir}
            onChange={(e) => setDir(e.target.value)}
            placeholder="/Users/you/OneDrive/precursor-backups"
            className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          <p className="text-[11px] text-muted mt-1">
            Absolute path to an existing, writable directory. Snapshots go under{" "}
            <code className="font-mono">db/</code> and attachment files under{" "}
            <code className="font-mono">blobs/</code>.
          </p>
        </div>

        <div>
          <label className="block text-xs text-muted mb-1">
            Snapshots to keep
          </label>
          <NumberInput
            value={retention}
            min={1}
            max={3650}
            onCommit={setRetention}
          />
          <p className="text-[11px] text-muted mt-1">
            Older dated database snapshots are pruned. Attachment files are kept
            indefinitely (they are de-duplicated, so this is cheap).
          </p>
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Status</h3>
        <div className="text-[11px] text-muted">
          <div>
            Last run: <span className="text-fg">{lastRunLabel}</span>
            {lastStatus && (
              <span
                className={
                  lastStatus === "ok"
                    ? " text-emerald-600 dark:text-emerald-400"
                    : " text-red-600 dark:text-red-400"
                }
              >
                {" "}
                ({lastStatus})
              </span>
            )}
          </div>
          {lastError && (
            <div className="text-red-600 dark:text-red-400 mt-1">{lastError}</div>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onRunNow}
            disabled={running || !dir}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface disabled:opacity-50"
          >
            {running ? "Backing up..." : "Back up now"}
          </button>
          {!dir && (
            <span className="text-[11px] text-muted">
              Set a backup folder first.
            </span>
          )}
        </div>

        {runResult && (
          <div
            className={
              "text-[11px] rounded border px-3 py-2 " +
              (runResult.ok
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300")
            }
          >
            {runResult.detail}
          </div>
        )}
      </section>
    </div>
  );
}

// A number input that lets the user type freely (including clearing the field
// and entering large values like 600000) and only clamps to [min, max] on blur,
// so an in-progress value isn't rewritten on every keystroke.
function NumberInput({
  value,
  min,
  max,
  onCommit,
}: {
  value: number;
  min: number;
  max: number;
  onCommit: (n: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  // Re-sync when the committed value changes (e.g. after clamping or reload).
  useEffect(() => {
    setDraft(String(value));
  }, [value]);
  return (
    <input
      type="number"
      min={min}
      max={max}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const n = Number(draft);
        const clamped = Number.isFinite(n)
          ? Math.max(min, Math.min(max, n))
          : min;
        onCommit(clamped);
        setDraft(String(clamped));
      }}
      className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
    />
  );
}

function SystemTab({
  sys,
  setSys,
  dockerAvailable,
}: {
  sys: SystemSettings;
  setSys: (next: SystemSettings) => void;
  dockerAvailable: boolean;
}) {
  const up = (patch: Partial<SystemSettings>) => setSys({ ...sys, ...patch });
  const numField = (
    label: string,
    key: keyof SystemSettings,
    opts: { min: number; max: number; help?: string },
  ) => (
    <div>
      <label className="block text-xs text-muted mb-1">{label}</label>
      <NumberInput
        value={sys[key] as number}
        min={opts.min}
        max={opts.max}
        onCommit={(n) => up({ [key]: n } as Partial<SystemSettings>)}
      />
      {opts.help && <p className="text-[11px] text-muted mt-1">{opts.help}</p>}
    </div>
  );
  const textField = (label: string, key: keyof SystemSettings, help?: string) => (
    <div>
      <label className="block text-xs text-muted mb-1">{label}</label>
      <input
        type="text"
        value={sys[key] as string}
        onChange={(e) => up({ [key]: e.target.value } as Partial<SystemSettings>)}
        className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
      {help && <p className="text-[11px] text-muted mt-1">{help}</p>}
    </div>
  );

  return (
    <div className="space-y-6">
      <p className="text-[11px] text-muted">
        These values default to the server's environment / .env and are
        overridden here at runtime. Most apply on the next chat or run; some
        scheduler internals still require a restart (only the run timeout below
        is live-applicable).
      </p>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Scheduler</h3>
        {numField(
          "Scheduled run timeout (seconds)",
          "scheduled_run_timeout_seconds",
          {
            min: 10,
            max: 86_400,
            help: "How long a single scheduled automation run may take before it is cancelled. Applied live on the next run.",
          },
        )}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Storage / retention</h3>
        {numField("Tool result retention (days)", "tool_result_retention_days", {
          min: 0,
          max: 3650,
          help: "How long full tool outputs are kept before their content is replaced with a short placeholder to save space. 0 = keep forever.",
        })}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Command runner (jail)</h3>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={sys.cmd_runner_jail}
            onChange={(e) => up({ cmd_runner_jail: e.target.checked })}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">Run commands in a Docker jail</span>
            <span className="block text-[11px] text-muted">
              Each command runs in a throwaway container with only its working
              directory mounted. Requires Docker.
            </span>
          </span>
        </label>

        {sys.cmd_runner_jail && !dockerAvailable && (
          <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
            Docker isn't available right now. The command-runner MCP server will
            refuse to enable until Docker is running (or jail mode is turned
            off).
          </div>
        )}

        {!sys.cmd_runner_jail && (
          <div className="text-[11px] rounded border border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400 px-3 py-2">
            <b>Jail disabled — full local disk access.</b> Commands run directly
            on the host with the backend's privileges and can read/modify any
            file the server user can. Only use this in a trusted, single-user
            environment.
          </div>
        )}

        {/* Container-only knobs — irrelevant when commands run on the host. */}
        {sys.cmd_runner_jail && (
          <>
            {textField(
              "Container image",
              "cmd_runner_image",
              "Docker image used for jailed runs. Use an image that bundles the interpreters you need (e.g. one with Node for JS).",
            )}
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={sys.cmd_runner_network}
                onChange={(e) => up({ cmd_runner_network: e.target.checked })}
                className="mt-0.5 accent-accent"
              />
              <span>
                <span className="block text-sm">Allow network access</span>
                <span className="block text-[11px] text-muted">
                  When off, jailed containers run with <code>--network none</code>.
                </span>
              </span>
            </label>
          </>
        )}

        {/* These apply in both modes (host run honours timeout + output cap). */}
        {numField("Command timeout (seconds)", "cmd_runner_timeout_seconds", {
          min: 1,
          max: 3600,
        })}
        {numField("Max output bytes", "cmd_runner_max_output_bytes", {
          min: 1000,
          max: 50_000_000,
          help: "stdout/stderr are truncated past this size.",
        })}

        {sys.cmd_runner_jail && (
          <>
            {textField(
              "Memory limit",
              "cmd_runner_memory",
              "Docker --memory value, e.g. 512m or 2g.",
            )}
            {numField("PID limit", "cmd_runner_pids_limit", {
              min: 1,
              max: 100_000,
              help: "Docker --pids-limit.",
            })}
            {textField(
              "CPU limit",
              "cmd_runner_cpus",
              "Docker --cpus value, e.g. 1 or 0.5.",
            )}
          </>
        )}
      </section>
    </div>
  );
}

function GitHubStatusBanner({ me }: { me: Me | null }) {
  if (me === null) {
    return (
      <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-border bg-surface text-xs text-muted">
        <span className="w-2 h-2 rounded-full bg-muted/60" />
        Checking GitHub connectivity…
      </div>
    );
  }
  // Identity resolved → token works and the GitHub Models provider can
  // call the API on the user's behalf.
  if (me.github) {
    return (
      <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-green-500/30 bg-green-500/10 text-xs">
        <span className="w-2 h-2 rounded-full bg-green-500" />
        <span className="text-text">
          Connected as{" "}
          <span className="font-medium">@{me.github.login}</span> — GitHub
          serves as the auth and models provider.
        </span>
      </div>
    );
  }
  // A token is configured but the API didn't return an identity. Usually
  // an expired/revoked token, network error, or insufficient scopes.
  if (me.github_token_source !== "none") {
    return (
      <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-amber-500/30 bg-amber-500/10 text-xs text-text">
        <span className="w-2 h-2 rounded-full bg-amber-500" />
        <span>
          Token detected ({me.github_token_source}) but identity could not be
          resolved. Verify the token is valid and has access to the GitHub
          Models API.
        </span>
      </div>
    );
  }
  return (
    <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-border bg-surface text-xs text-muted">
      <span className="w-2 h-2 rounded-full bg-muted/60" />
      Not connected — set a personal access token below, export
      <code className="mx-1 font-mono">GITHUB_TOKEN</code>, or run
      <code className="ml-1 font-mono">gh auth login</code>.
    </div>
  );
}

// Precursor capability sections served over the built-in "precursor" MCP
// server, with a short label + whether they perform writes.
const EXPOSE_SECTIONS: ReadonlyArray<{
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
  { key: "skills", label: "Skills", hint: "List skills & read their instructions." },
  { key: "memory", label: "Memory", hint: "Read long-term memory entries." },
  {
    key: "memory_write",
    label: "Edit memory",
    hint: "Let callers add and edit long-term memory entries.",
    danger: true,
  },
  {
    key: "post_message",
    label: "Post message",
    hint: "Let callers post to a topic and run a full assistant turn.",
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
];

// Per-section enablement for the built-in "precursor" MCP server, which serves
// Precursor's own capabilities to the in-app agent and external MCP hosts.
function McpExposeCard({
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
  const anyWriteOn =
    expose.memory_write || expose.post_message || expose.schedules || expose.reminders;
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
              (post messages, manage schedules). Only enable for trusted hosts.
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

function McpServerCard({
  server,
  onToggle,
  onReload,
  onTogglePreview,
  onReauthenticate,
  onEdit,
  onDelete,
}: {
  server: MCPServerStatus;
  onToggle: () => void;
  onReload?: () => void;
  onTogglePreview?: () => void;
  onReauthenticate?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const stateColor =
    server.state === "connected" || server.state === "ready"
      ? "text-green-500"
      : server.state === "error"
        ? "text-red-500"
        : server.state === "needs_auth"
          ? "text-amber-500"
          : server.state === "connecting"
            ? "text-amber-500"
            : "text-muted";

  return (
    <li className="border border-border rounded">
      <div className="flex items-center justify-between px-2 py-1.5 gap-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 text-left flex-1 min-w-0"
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <div className="min-w-0">
            <div className="text-sm flex items-center gap-1.5">
              <span>{server.name}</span>
              {server.builtin && (
                <span className="text-[10px] text-muted border border-border rounded px-1 py-px">
                  built-in
                </span>
              )}
            </div>
            <div className={`text-[11px] ${stateColor}`}>
              {server.transport} — {server.state}
              {server.tools.length > 0 && ` · ${server.tools.length} tools`}
            </div>
          </div>
        </button>
        <label className="flex items-center gap-1 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={server.enabled}
            onChange={onToggle}
            className="accent-accent"
          />
          <span className="text-muted">enabled</span>
        </label>
        {onTogglePreview && (
          <label
            className="flex items-center gap-1 text-xs cursor-pointer"
            data-tooltip="Use the hosted WorkIQ endpoint (OAuth sign-in) to enable writes"
          >
            <input
              type="checkbox"
              checked={server.preview ?? false}
              onChange={onTogglePreview}
              className="accent-accent"
            />
            <span className="text-muted">preview</span>
          </label>
        )}
        {onReauthenticate && server.preview && (
          <button
            type="button"
            onClick={onReauthenticate}
            disabled={server.state === "connecting"}
            data-tooltip="Open the WorkIQ browser sign-in to refresh expired credentials"
            className={`flex items-center gap-1 px-2 py-1 rounded text-xs whitespace-nowrap disabled:opacity-50 ${
              server.state === "needs_auth"
                ? "bg-accent text-white"
                : "border border-border text-muted hover:text-text"
            }`}
          >
            <LogIn size={12} />
            {server.state === "needs_auth" ? "Sign in" : "Re-authenticate"}
          </button>
        )}
        {onReload && (
          <button
            type="button"
            onClick={onReload}
            disabled={server.state === "connecting"}
            data-tooltip="Reload this server's tool catalogue"
            aria-label="Reload tools"
            className="p-1 rounded hover:bg-bg text-muted hover:text-text disabled:opacity-50"
          >
            <RefreshCw size={14} />
          </button>
        )}
        {!server.builtin && onEdit && (
          <button
            type="button"
            onClick={onEdit}
            data-tooltip="Edit"
            aria-label="Edit server"
            className="p-1 rounded hover:bg-bg text-muted hover:text-text"
          >
            <Pencil size={14} />
          </button>
        )}
        {!server.builtin && onDelete && (
          <button
            type="button"
            onClick={onDelete}
            data-tooltip="Delete"
            aria-label="Delete server"
            className="p-1 rounded hover:bg-bg text-muted hover:text-red-500"
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>
      {open && (
        <div className="border-t border-border px-3 py-2 text-xs space-y-2">
          {server.url && (
            <div>
              <span className="text-muted">URL:</span>{" "}
              <span className="font-mono break-all">{server.url}</span>
            </div>
          )}
          {server.command && (
            <div>
              <span className="text-muted">Command:</span>{" "}
              <span className="font-mono break-all">{server.command}</span>
            </div>
          )}
          {server.error && (
            <div
              className={`whitespace-pre-wrap break-words ${
                server.state === "needs_auth" ? "text-amber-500" : "text-red-500"
              }`}
            >
              {server.error}
            </div>
          )}
          {server.tools.length === 0 ? (
            <div className="text-muted italic">
              {server.state === "needs_auth"
                ? "WorkIQ sign-in expired — select Sign in to re-authenticate."
                : server.enabled
                  ? "Use the reload button to refresh the tool catalogue."
                  : "Enable the server to discover its tools."}
            </div>
          ) : (
            <div>
              <div className="text-muted mb-1">Available tools</div>
              <ul className="space-y-1 max-h-60 overflow-y-auto pr-1">
                {server.tools.map((t) => (
                  <li key={t.name}>
                    <div className="font-mono text-[11px]">{t.name}</div>
                    {t.description && (
                      <div className="text-[11px] text-muted">{t.description}</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function McpServerEditor({
  server,
  onClose,
  onSaved,
}: {
  server: MCPServerStatus | null;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const isNew = server === null;
  const [name, setName] = useState(server?.name ?? "");
  const [transport, setTransport] = useState<"streamable_http" | "stdio">(
    (server?.transport as "streamable_http" | "stdio") ?? "streamable_http",
  );
  const [url, setUrl] = useState(server?.url ?? "");
  const [command, setCommand] = useState(server?.command_bin ?? "");
  const [argsList, setArgsList] = useState<string[]>(server?.args ?? []);
  // Existing header values are not exposed by the API for security; keys are
  // shown so the user knows what is set, but values must be re-entered.
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>(() =>
    server?.header_keys?.map((k) => ({ key: k, value: "" })) ?? [],
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSave(): Promise<void> {
    setError(null);
    setSaving(true);
    try {
      const headerMap: Record<string, string> = {};
      for (const h of headers) {
        if (h.key.trim()) headerMap[h.key.trim()] = h.value;
      }
      const cleanArgs = argsList.map((a) => a).filter((a) => a !== undefined);

      if (isNew) {
        const payload: MCPServerCreate = {
          name: name.trim(),
          transport,
          ...(transport === "streamable_http"
            ? { url: url.trim(), headers: headerMap }
            : { command: command.trim(), args: cleanArgs }),
        };
        await api.mcp.create(payload);
      } else if (server?.id != null) {
        const payload: MCPServerUpdate = {
          name: name.trim(),
          transport,
          ...(transport === "streamable_http"
            ? { url: url.trim(), headers: headerMap }
            : { command: command.trim(), args: cleanArgs }),
        };
        await api.mcp.update(server.id, payload);
      }
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bg border border-border rounded-lg w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between p-3 border-b border-border">
          <h3 className="text-sm font-medium">
            {isNew ? "New MCP server" : `Edit ${server?.name}`}
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface text-muted"
          >
            <X size={16} />
          </button>
        </header>
        <div className="p-3 space-y-3 overflow-y-auto">
          <div>
            <label className="block text-xs text-muted mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-server"
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
            <p className="text-[10px] text-muted mt-1">
              Lowercase letters, digits, hyphens. Max 64 chars.
            </p>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Transport</label>
            <div className="flex gap-3 text-xs">
              <label className="flex items-center gap-1 cursor-pointer">
                <input
                  type="radio"
                  name="transport"
                  checked={transport === "streamable_http"}
                  onChange={() => setTransport("streamable_http")}
                  className="accent-accent"
                />
                HTTP (streamable)
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input
                  type="radio"
                  name="transport"
                  checked={transport === "stdio"}
                  onChange={() => setTransport("stdio")}
                  className="accent-accent"
                />
                stdio (subprocess)
              </label>
            </div>
          </div>

          {transport === "streamable_http" ? (
            <>
              <div>
                <label className="block text-xs text-muted mb-1">URL</label>
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/mcp"
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent font-mono"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs text-muted">Headers</label>
                  <button
                    type="button"
                    onClick={() =>
                      setHeaders((prev) => [...prev, { key: "", value: "" }])
                    }
                    className="text-[10px] text-accent hover:underline"
                  >
                    + Add header
                  </button>
                </div>
                {!isNew && server?.header_keys?.length ? (
                  <p className="text-[10px] text-muted mb-1">
                    Existing header values are hidden — re-enter them to keep
                    them, or remove the row to drop them.
                  </p>
                ) : null}
                <div className="space-y-1">
                  {headers.map((h, i) => (
                    <div key={i} className="flex gap-1">
                      <input
                        type="text"
                        value={h.key}
                        onChange={(e) =>
                          setHeaders((prev) =>
                            prev.map((x, j) =>
                              j === i ? { ...x, key: e.target.value } : x,
                            ),
                          )
                        }
                        placeholder="Header-Name"
                        className="flex-1 bg-surface border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent"
                      />
                      <input
                        type="text"
                        value={h.value}
                        onChange={(e) =>
                          setHeaders((prev) =>
                            prev.map((x, j) =>
                              j === i ? { ...x, value: e.target.value } : x,
                            ),
                          )
                        }
                        placeholder="value"
                        className="flex-1 bg-surface border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          setHeaders((prev) => prev.filter((_, j) => j !== i))
                        }
                        className="p-1 rounded hover:bg-surface text-muted hover:text-red-500"
                        data-tooltip="Remove"
                        aria-label="Remove header"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="block text-xs text-muted mb-1">Command</label>
                <input
                  type="text"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx"
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent font-mono"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs text-muted">Arguments</label>
                  <button
                    type="button"
                    onClick={() => setArgsList((prev) => [...prev, ""])}
                    className="text-[10px] text-accent hover:underline"
                  >
                    + Add arg
                  </button>
                </div>
                <div className="space-y-1">
                  {argsList.map((a, i) => (
                    <div key={i} className="flex gap-1">
                      <input
                        type="text"
                        value={a}
                        onChange={(e) =>
                          setArgsList((prev) =>
                            prev.map((x, j) => (j === i ? e.target.value : x)),
                          )
                        }
                        className="flex-1 bg-surface border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          setArgsList((prev) => prev.filter((_, j) => j !== i))
                        }
                        className="p-1 rounded hover:bg-surface text-muted hover:text-red-500"
                        aria-label="Remove"
                        data-tooltip="Remove"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {error && (
            <div className="text-xs text-red-500 whitespace-pre-wrap break-words">
              {error}
            </div>
          )}
        </div>
        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving..." : isNew ? "Create" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}
