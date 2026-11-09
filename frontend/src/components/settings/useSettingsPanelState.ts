import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Palette } from "lucide-react";
import { api } from "../../lib/api";
import { mcpAuthStore } from "../../lib/mcpAuth";
import { mcpAuthFamily } from "../../lib/mcpServers";
import { signInWorkiq } from "../../lib/workiqSignIn";
import { setTheme, getStoredTheme, type Theme } from "../../lib/theme";
import { setFont, getStoredFont, type FontChoice } from "../../lib/font";
import { modelsStore } from "../../lib/modelsStore";
import { settingsStore } from "../../lib/settingsStore";
import type { BackupRunResult, LLMModel, LLMProviderSpec, MCPServerStatus, Me, ConversationApprovalPolicy, Settings } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { resolveSettingsPages } from "../../lib/plugins";
import { usePluginDescriptors } from "../../lib/pluginStore";
import { SystemSettings, pickSystem } from "./systemSettings";
import { SettingsPanelProps, Category, CategoryDef, CATEGORIES, PLUGIN_TAB_PREFIX } from "./panelConfig";

/**
 * State and handlers of the settings panel, shared by its tab components.
 * Each tab receives the whole object (`SettingsPanelState`) and destructures
 * what it uses, so moving a tab out of `SettingsPanel` needs no prop plumbing.
 */
export function useSettingsPanelState({
  onClose: closeNow,
  initialCategory,
  onCollectionsChanged,
}: SettingsPanelProps) {
  const confirmAction = useConfirm();
  const [category, setCategory] = useState<Category>(initialCategory ?? "appearance");
  // Settings pages contributed by installed plugins, appended as their own group.
  const pluginDescriptors = usePluginDescriptors();
  const pluginPages = useMemo(
    () => resolveSettingsPages(pluginDescriptors),
    [pluginDescriptors],
  );
  const allCategories = useMemo<ReadonlyArray<CategoryDef>>(
    () => [
      ...CATEGORIES,
      ...pluginPages.map((page) => ({
        id: `${PLUGIN_TAB_PREFIX}${page.id}`,
        label: page.label,
        icon: page.icon as typeof Palette,
        group: "Plugins" as const,
      })),
    ],
    [pluginPages],
  );
  const activePluginPage = useMemo(
    () =>
      pluginPages.find((p) => `${PLUGIN_TAB_PREFIX}${p.id}` === category) ?? null,
    [pluginPages, category],
  );
  const [settings, setSettings] = useState<Settings | null>(null);
  const [mcp, setMcp] = useState<MCPServerStatus[]>([]);
  const [mcpLoading, setMcpLoading] = useState(true);
  const [theme, setThemeState] = useState<Theme>(getStoredTheme());
  const [font, setFontState] = useState<FontChoice>(getStoredFont());
  const [models, setModels] = useState<LLMModel[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const modelsRequest = useRef(0);
  useEffect(() => () => { modelsRequest.current++; }, []);
  const [provider, setProvider] = useState("");
  const [defaultProvider, setDefaultProvider] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [defaultModels, setDefaultModels] = useState<LLMModel[]>([]);
  const [providers, setProviders] = useState<LLMProviderSpec[]>([]);
  const [modelFallbacks, setModelFallbacks] = useState<Settings["model_fallbacks"]>({ categories: [] });
  const [providerConfig, setProviderConfig] = useState<
    Record<string, Record<string, string>>
  >({});
  // Stored provider secrets ("provider.field") queued for removal on save. The
  // server never returns secrets, so an empty input alone can't mean "clear".
  const [clearedSecrets, setClearedSecrets] = useState<string[]>([]);
  const [repo, setRepo] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [azureEndpoint, setAzureEndpoint] = useState("");
  const [azureLanguage, setAzureLanguage] = useState("");
  const [azureKey, setAzureKey] = useState("");
  const [liveFastModel, setLiveFastModel] = useState("");
  const [liveReasoningEffort, setLiveReasoningEffort] = useState("");
  const [liveEnabled, setLiveEnabled] = useState(true);
  const [liveTranscriptRetentionDays, setLiveTranscriptRetentionDays] = useState(7);
  // Auto-naming a fresh chat from its first message, and the model that does it
  // ("" = the default chat model).
  const [chatAutonameEnabled, setChatAutonameEnabled] = useState(true);
  const [mockEnabled, setMockEnabled] = useState(true);
  const [chatAutonameModel, setChatAutonameModel] = useState("");
  const [sttTest, setSttTest] = useState<
    { state: "idle" | "testing" | "ok" | "error"; detail?: string }
  >({ state: "idle" });
  const [ttlMinutes, setTtlMinutes] = useState(60);
  const [showChatStats, setShowChatStats] = useState(true);
  const [showQuickActions, setShowQuickActions] = useState(true);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [completionSoundEnabled, setCompletionSoundEnabled] = useState(false);
  const [maxToolRounds, setMaxToolRounds] = useState(15);
  const [scheduledApprovalTimeout, setScheduledApprovalTimeout] = useState(60);
  const [topicApprovalPolicy, setTopicApprovalPolicy] = useState<ConversationApprovalPolicy>("autonomous");
  const [chatApprovalPolicy, setChatApprovalPolicy] = useState<ConversationApprovalPolicy>("autonomous");
  const [issueAssociationsEnabled, setIssueAssociationsEnabled] = useState(true);
  // Folder backup (DB + blobs + definitions) — editable prefs plus a manual "run now" action.
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
  const [deferTools, setDeferTools] = useState(true);
  const [alwaysLoad, setAlwaysLoad] = useState<Record<string, boolean>>({});
  const [workiqTenant, setWorkiqTenant] = useState("");
  // Browser channel the built-in Playwright MCP server drives (MCP tab).
  const [playwrightBrowser, setPlaywrightBrowser] = useState("msedge");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [mcpEditing, setMcpEditing] = useState<MCPServerStatus | "new" | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [appVersion, setAppVersion] = useState<string | null>(null);

  // One sign-in control per *credential*, not per server. The Agent 365
  // endpoints share a single Entra token, so a button on each row invited six
  // sign-ins for the two credentials Precursor actually holds. The first server
  // of each auth family owns the button; its siblings say who they sign in with.
  const signInOwner = useMemo(() => {
    const owner = new Map<string, string>();
    for (const server of mcp) {
      if (!server.oauth) continue;
      const family = mcpAuthFamily(server.name);
      if (!owner.has(family)) owner.set(family, server.name);
    }
    return owner;
  }, [mcp]);

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

  // `share` seeds the shared store used by the composer picker — only safe when
  // the fetched list reflects the *saved* provider, not an unsaved preview.
  async function loadModels(
    providerOverride: string,
    { share = false }: { share?: boolean } = {},
  ): Promise<LLMModel[]> {
    const request = ++modelsRequest.current;
    setModelsLoading(true);
    setModelsError(null);
    setModels([]);
    try {
      const list = await api.llm.listModels(providerOverride);
      if (request !== modelsRequest.current) return list;
      setModels(list);
      modelsStore.adopt(list, providerOverride);
      if (share) modelsStore.applySettings(settingsStore.current());
      return list;
    } catch (e) {
      if (request === modelsRequest.current) {
        setModels([]);
        setModelsError(e instanceof Error ? e.message : String(e));
      }
      return [];
    } finally {
      if (request === modelsRequest.current) setModelsLoading(false);
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
    if (!defaultProvider) return;
    let stale = false;
    setDefaultModels([]);
    api.llm.listModels(defaultProvider).then(
      (list) => { if (!stale) setDefaultModels(list); },
      () => {},
    );
    return () => { stale = true; };
  }, [defaultProvider]);

  const liveSettings = settings ? { ...settings, mock_provider_enabled: mockEnabled } : settings;

  const activeProviderSpec = providers.find((p) => p.id === provider);

  useEffect(() => {
    void (async () => {
      const s = await api.settings.get();
      setSettings(s);
      setProvider(s.llm_provider);
      setDefaultProvider(s.llm_provider);
      setDefaultModel(s.llm_model_category ? "" : s.llm_model);
      setModelFallbacks(s.model_fallbacks ?? { categories: [] });
      setProviderConfig(s.llm_providers ?? {});
      setRepo(s.github_repo);
      setTtlMinutes(s.issue_context_ttl_minutes);
      setShowChatStats(s.show_chat_stats);
      setShowQuickActions(s.show_quick_actions ?? true);
      setNotificationsEnabled(s.notifications_enabled);
      setCompletionSoundEnabled(s.completion_sound_enabled);
      setMaxToolRounds(s.max_tool_rounds);
      setScheduledApprovalTimeout(s.scheduled_approval_timeout_minutes ?? 60);
      setTopicApprovalPolicy(s.topic_approval_policy ?? "autonomous");
      setChatApprovalPolicy(s.chat_approval_policy ?? "autonomous");
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
      setChatAutonameEnabled(s.chat_autoname_enabled);
      setMockEnabled(s.mock_provider_enabled ?? true);
      setChatAutonameModel(s.chat_autoname_model);
      setSys(pickSystem(s));
      setDockerAvailable(s.docker_available);
      setExpose(s.mcp_expose ?? {});
      setHttpEnabled(s.mcp_http_enabled);
      setDeferTools(s.mcp_defer_tools ?? true);
      setAlwaysLoad(s.mcp_always_load ?? {});
      // A discovered tenant shows as a placeholder rather than typed input, so
      // saving doesn't silently pin it into the database.
      setWorkiqTenant(s.workiq_tenant_discovered ? "" : s.workiq_tenant_id);
      setPlaywrightBrowser(s.playwright_browser);
      settingsStore.set(s);
      await refreshMcp();
      try {
        setProviders(await api.llm.listProviders(true));
      } catch {
        setProviders([]);
      }
      await loadModels(s.llm_provider, { share: true });
      try {
        setMe(await api.me.get());
      } catch {
        setMe({ github: null, github_token_source: "none", proxy_user: null });
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
      for (const marker of clearedSecrets) {
        const [cleared, field] = marker.split(".");
        if (cleared === pid && !entry[field]) entry[field] = "";
      }
      if (Object.keys(entry).length > 0) out[pid] = entry;
    }
    return out;
  }

  // Persist provider config without changing the default, then refresh the catalog — without
  // closing the panel. The server resolves retired pins; don't overwrite a
  // categorised pin merely because it is absent from the refreshed catalogue.
  async function applyProviderSettings(): Promise<void> {
    setModelsLoading(true);
    try {
      const updated = await api.settings.update({
        llm_providers: buildLlmProvidersPayload(),
      });
      setSettings(updated);
      setProviderConfig(updated.llm_providers ?? {});
      setClearedSecrets([]);
      settingsStore.set(updated);
      await loadModels(provider, { share: true });
      modelsStore.applySettings(updated);
    } catch (e) {
      setModelsError(e instanceof Error ? e.message : String(e));
      setModelsLoading(false);
    }
  }

  async function save(): Promise<void> {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const payload: Parameters<typeof api.settings.update>[0] = {
        theme,
        font_family: font,
        llm_provider: defaultProvider,
        github_repo: repo,
        model_fallbacks: modelFallbacks,
        issue_context_ttl_minutes: ttlMinutes,
        show_chat_stats: showChatStats,
        show_quick_actions: showQuickActions,
        notifications_enabled: notificationsEnabled,
        completion_sound_enabled: completionSoundEnabled,
        max_tool_rounds: maxToolRounds,
        topic_approval_policy: topicApprovalPolicy,
        chat_approval_policy: chatApprovalPolicy,
        scheduled_approval_timeout_minutes: scheduledApprovalTimeout,
        issue_associations_enabled: issueAssociationsEnabled,
        azure_speech_endpoint: azureEndpoint,
        azure_speech_language: azureLanguage,
        live_fast_model: liveFastModel,
        live_reasoning_effort: liveReasoningEffort,
        live_enabled: liveEnabled,
        live_transcript_retention_days: liveTranscriptRetentionDays,
        chat_autoname_enabled: chatAutonameEnabled,
        mock_provider_enabled: mockEnabled,
        chat_autoname_model: chatAutonameModel,
        mcp_expose: expose,
        mcp_http_enabled: httpEnabled,
        mcp_defer_tools: deferTools,
        mcp_always_load: alwaysLoad,
        workiq_tenant_id: workiqTenant.trim(),
        playwright_browser: playwrightBrowser,
        backup_enabled: backupEnabled,
        backup_dir: backupDir,
        backup_retention: backupRetention,
        ...(sys ?? {}),
      };
      const llmProviders = buildLlmProvidersPayload();
      if (defaultProvider !== settings?.llm_provider) {
        payload.llm_model = "";
        payload.llm_model_category = null;
        payload.llm_reasoning_effort = "";
      } else if (defaultModel && defaultModel !== settings?.llm_model) {
        payload.llm_model = defaultModel;
        payload.llm_model_category = null;
        payload.llm_reasoning_effort = "";
      }
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
      for (const configuredProvider of Object.keys(llmProviders)) void modelsStore.refresh(configuredProvider);
      setSettings(updated);
      modelsStore.applySettings(updated);
      // A new GitHub token can mean a different account/plan, hence a different
      // catalog — the composer picker must not keep the previous one.
      if (apiKeys.github_token) void modelsStore.refresh();
      settingsStore.set(updated);
      setGithubToken((current) => current === githubToken ? "" : current);
      setAzureKey((current) => current === azureKey ? "" : current);
      const savedProviderConfig = updated.llm_providers ?? {};
      setProviderConfig((current) => current === providerConfig ? savedProviderConfig : current);
      setClearedSecrets((current) => current === clearedSecrets ? [] : current);
      const savedDefaultModel = updated.llm_model_category ? "" : updated.llm_model;
      setDefaultModel((current) => current === defaultModel ? savedDefaultModel : current);
      setTheme(theme);
      setFont(font);
      baseline.current = captureFormSnapshot("", "", savedProviderConfig, savedDefaultModel);
      setSaved(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
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
        definitions_archive: null,
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
      const next = await signInWorkiq(name);
      if (!next) {
        // The user abandoned the sign-in (closed the popup) — restore the
        // needs-auth state without an error.
        setMcp((prev) =>
          prev.map((s) => (s.name === name ? { ...s, state: "needs_auth" } : s)),
        );
        return;
      }
      setMcp((prev) => prev.map((s) => (s.name === name ? next : s)));
      // Clear just this credential's notice; the store spends the fresh SSO
      // cookie on a hands-free pass for any sibling that is still stale.
      mcpAuthStore.resolve(name);
      // The backend adopted every server sharing this credential, so re-read the
      // list — otherwise the siblings keep showing the sign-in they just got.
      void refreshMcp();
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

  function captureFormSnapshot(
    githubSecret = githubToken,
    azureSecret = azureKey,
    llmConfig = providerConfig,
    model = defaultModel,
  ): string {
    return JSON.stringify([
      theme, font, defaultProvider, model, mockEnabled, modelFallbacks, llmConfig, clearedSecrets,
      repo, githubSecret, azureSecret, azureEndpoint, azureLanguage, ttlMinutes, showChatStats, showQuickActions,
      notificationsEnabled, completionSoundEnabled, maxToolRounds, topicApprovalPolicy, chatApprovalPolicy, scheduledApprovalTimeout, issueAssociationsEnabled,
      liveFastModel, liveReasoningEffort, liveEnabled, liveTranscriptRetentionDays,
      chatAutonameEnabled, chatAutonameModel, expose, httpEnabled, workiqTenant,
      playwrightBrowser, backupEnabled, backupDir, backupRetention, sys,
    ]);
  }
  const formSnapshot = captureFormSnapshot();
  // Captured on the render right after the initial load populates the form.
  const baseline = useRef<string | null>(null);
  useEffect(() => {
    if (settings && baseline.current === null) baseline.current = formSnapshot;
  }, [settings, formSnapshot]);

  const confirming = useRef(false);
  const onClose = useCallback(async (): Promise<void> => {
    if (confirming.current) return;
    const dirty = baseline.current !== null && baseline.current !== formSnapshot;
    if (dirty) confirming.current = true;
    const keep = dirty && !(await confirmAction({
      title: "Discard unsaved changes?",
      message: "You have unsaved settings. Closing now will discard them.",
      confirmLabel: "Discard changes",
      cancelLabel: "Keep editing",
      variant: "warning",
    }));
    confirming.current = false;
    if (keep) return;
    closeNow();
  }, [formSnapshot, confirmAction, closeNow]);

  // Esc closes the modal — convention shared with most settings dialogs
  // (VS Code, Linear, GitHub).
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return {
    closeNow,
    initialCategory,
    onCollectionsChanged,
    confirmAction,
    category,
    setCategory,
    pluginDescriptors,
    pluginPages,
    allCategories,
    activePluginPage,
    settings,
    setSettings,
    mcp,
    setMcp,
    mcpLoading,
    setMcpLoading,
    theme,
    setThemeState,
    font,
    setFontState,
    models,
    setModels,
    modelsError,
    setModelsError,
    modelsLoading,
    setModelsLoading,
    modelsRequest,
    provider,
    setProvider,
    defaultProvider,
    setDefaultProvider,
    defaultModel,
    setDefaultModel,
    defaultModels,
    setDefaultModels,
    providers,
    setProviders,
    modelFallbacks,
    setModelFallbacks,
    providerConfig,
    setProviderConfig,
    clearedSecrets,
    setClearedSecrets,
    repo,
    setRepo,
    githubToken,
    setGithubToken,
    azureEndpoint,
    setAzureEndpoint,
    azureLanguage,
    setAzureLanguage,
    azureKey,
    setAzureKey,
    liveFastModel,
    setLiveFastModel,
    liveReasoningEffort,
    setLiveReasoningEffort,
    liveEnabled,
    setLiveEnabled,
    liveTranscriptRetentionDays,
    setLiveTranscriptRetentionDays,
    chatAutonameEnabled,
    setChatAutonameEnabled,
    mockEnabled,
    setMockEnabled,
    chatAutonameModel,
    setChatAutonameModel,
    sttTest,
    setSttTest,
    ttlMinutes,
    setTtlMinutes,
    showChatStats,
    setShowChatStats,
    showQuickActions,
    setShowQuickActions,
    notificationsEnabled,
    setNotificationsEnabled,
    completionSoundEnabled,
    setCompletionSoundEnabled,
    maxToolRounds,
    setMaxToolRounds,
    scheduledApprovalTimeout,
    setScheduledApprovalTimeout,
    topicApprovalPolicy,
    setTopicApprovalPolicy,
    chatApprovalPolicy,
    setChatApprovalPolicy,
    issueAssociationsEnabled,
    setIssueAssociationsEnabled,
    backupEnabled,
    setBackupEnabled,
    backupDir,
    setBackupDir,
    backupRetention,
    setBackupRetention,
    backupRunning,
    setBackupRunning,
    backupRunResult,
    setBackupRunResult,
    sys,
    setSys,
    dockerAvailable,
    setDockerAvailable,
    expose,
    setExpose,
    httpEnabled,
    setHttpEnabled,
    deferTools,
    setDeferTools,
    alwaysLoad,
    setAlwaysLoad,
    workiqTenant,
    setWorkiqTenant,
    playwrightBrowser,
    setPlaywrightBrowser,
    saving,
    setSaving,
    saveError,
    setSaveError,
    saved,
    setSaved,
    mcpEditing,
    setMcpEditing,
    me,
    setMe,
    appVersion,
    setAppVersion,
    signInOwner,
    refreshMcp,
    loadModels,
    onProviderChange,
    liveSettings,
    activeProviderSpec,
    buildLlmProvidersPayload,
    applyProviderSettings,
    save,
    runBackupNow,
    testStt,
    toggleMcp,
    togglePreview,
    reloadMcpTools,
    reauthenticateWorkiq,
    captureFormSnapshot,
    formSnapshot,
    baseline,
    confirming,
    onClose,
  };
}

export type SettingsPanelState = ReturnType<typeof useSettingsPanelState>;
