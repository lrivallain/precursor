import type { ReactNode } from "react";
import { X } from "lucide-react";
import { SidebarTabs } from "./SidebarTabs";
import { SkillsTab } from "./SkillsTab";
import { RolesTab } from "./RolesTab";
import { CollectionsTab } from "./CollectionsTab";
import { MemoriesTab } from "./MemoriesTab";
import { StatsTab } from "./StatsTab";
import { AgentsSettings } from "./AgentsSettings";
import { PluginsSettings } from "./PluginsSettings";
import { WorkflowsSettings } from "./WorkflowsSettings";
import { IQSettings } from "./IQSettings";
import { PresetsSettings } from "./PresetsSettings";
import { DefinitionsMigrationWizard } from "./DefinitionsMigrationWizard";
import { SystemTab } from "./settings/SystemTab";
import { BackupTab } from "./settings/BackupTab";
import { SETTINGS_GROUPS, type Category, type CoreCategory, type SettingsPanelProps } from "./settings/panelConfig";
import { AppearanceTab } from "./settings/AppearanceTab";
import { ChatTab } from "./settings/ChatTab";
import { ModelTab } from "./settings/ModelTab";
import { GitHubTab } from "./settings/GitHubTab";
import { SpeechTab } from "./settings/SpeechTab";
import { LiveTab } from "./settings/LiveTab";
import { McpTab } from "./settings/McpTab";
import { useAppearanceSettings } from "./settings/useAppearanceSettings";
import { useAppVersion } from "./settings/useAppVersion";
import { useBackupSettings } from "./settings/useBackupSettings";
import { useChatSettings } from "./settings/useChatSettings";
import { useGitHubSettings } from "./settings/useGitHubSettings";
import { useLiveSettings } from "./settings/useLiveSettings";
import { useMcpSettings } from "./settings/useMcpSettings";
import { useModelSettings } from "./settings/useModelSettings";
import { usePresetsSettings } from "./settings/usePresetsSettings";
import { useSettingsCategories } from "./settings/useSettingsCategories";
import { useLoadedSettings, useSettingsForm, type LoadedSettings } from "./settings/useSettingsForm";
import { useSpeechSettings } from "./settings/useSpeechSettings";
import { useSystemSettings } from "./settings/useSystemSettings";

// Every tab's state lives here, above the tab components: only the active tab
// is mounted, but its edits must survive switching tabs and one Save sends all.
function useSettingsTabs(loaded: LoadedSettings) {
  return {
    appearance: useAppearanceSettings(),
    chat: useChatSettings(),
    presets: usePresetsSettings(),
    model: useModelSettings(loaded),
    github: useGitHubSettings(),
    speech: useSpeechSettings(),
    live: useLiveSettings(),
    mcp: useMcpSettings(),
    backup: useBackupSettings(loaded),
    system: useSystemSettings(),
    version: useAppVersion(),
  };
}

interface TabContext {
  tabs: ReturnType<typeof useSettingsTabs>;
  loaded: LoadedSettings;
  setCategory: (next: Category) => void;
  onCollectionsChanged: SettingsPanelProps["onCollectionsChanged"];
}

const TAB_CONTENT: Record<CoreCategory, (ctx: TabContext) => ReactNode> = {
  appearance: ({ tabs }) => <AppearanceTab s={tabs.appearance} />,
  chat: ({ tabs }) => <ChatTab s={tabs.chat} models={tabs.model.models} />,
  presets: ({ tabs, loaded }) => (
    <PresetsSettings
      value={tabs.presets.modelFallbacks}
      onChange={tabs.presets.setModelFallbacks}
      settings={loaded.settings}
      providers={tabs.model.providers}
    />
  ),
  model: ({ tabs }) => <ModelTab m={tabs.model} system={tabs.system} />,
  github: ({ tabs, loaded }) => <GitHubTab s={tabs.github} settings={loaded.settings} />,
  speech: ({ tabs, loaded }) => <SpeechTab s={tabs.speech} settings={loaded.settings} />,
  live: ({ tabs }) => <LiveTab s={tabs.live} models={tabs.model.models} />,
  iq: ({ tabs, loaded, setCategory }) => (
    <IQSettings
      settings={loaded.settings}
      onSettings={loaded.adopt}
      providers={tabs.model.providers}
      onNavigate={setCategory}
    />
  ),
  mcp: ({ tabs, loaded }) => <McpTab s={tabs.mcp} settings={loaded.settings} />,
  skills: () => <SkillsTab />,
  roles: ({ setCategory }) => <RolesTab onOpenMigration={() => setCategory("definitions")} />,
  collections: ({ onCollectionsChanged }) => <CollectionsTab onChanged={onCollectionsChanged} />,
  memory: () => <MemoriesTab />,
  agents: () => <AgentsSettings />,
  plugins: () => <PluginsSettings />,
  workflows: ({ setCategory }) => <WorkflowsSettings onOpenMigration={() => setCategory("definitions")} />,
  definitions: () => <DefinitionsMigrationWizard />,
  stats: () => <StatsTab />,
  backup: ({ tabs: { backup }, loaded: { settings } }) => (
    <BackupTab
      enabled={backup.enabled}
      setEnabled={backup.setEnabled}
      dir={backup.dir}
      setDir={backup.setDir}
      retention={backup.retention}
      setRetention={backup.setRetention}
      lastRunAt={settings?.backup_last_run_at ?? null}
      lastStatus={settings?.backup_last_status ?? null}
      lastError={settings?.backup_last_error ?? null}
      running={backup.running}
      runResult={backup.runResult}
      onRunNow={() => void backup.runNow()}
    />
  ),
  system: ({ tabs: { system } }) =>
    system.sys && <SystemTab sys={system.sys} setSys={system.setSys} dockerAvailable={system.dockerAvailable} />,
};

function tabContent(category: Category, ctx: TabContext): ReactNode {
  return Object.hasOwn(TAB_CONTENT, category) ? TAB_CONTENT[category as CoreCategory](ctx) : null;
}

export function SettingsPanel({ onClose: closeNow, initialCategory, onCollectionsChanged }: SettingsPanelProps) {
  const { category, setCategory, allCategories, activePluginPage } = useSettingsCategories(initialCategory);
  const loaded = useLoadedSettings();
  const tabs = useSettingsTabs(loaded);
  const form = useSettingsForm({
    loaded,
    sections: Object.values(tabs).map((tab) => tab.section),
    onClose: closeNow,
  });
  const { onClose } = form;
  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) void onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-panel-title"
        className="bg-bg border border-border rounded-lg shadow-2xl flex flex-col w-full overflow-hidden"
        style={{ maxWidth: 960, height: "min(720px, 90vh)" }}
      >
        <header className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0">
          <h2 id="settings-panel-title" className="font-semibold">
            Settings
          </h2>
          <button
            onClick={() => void onClose()}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close (Esc)"
          >
            <X size={18} />
          </button>
        </header>

        <div className="flex flex-1 min-h-0">
          <SidebarTabs
            className="w-12 sm:w-52 shrink-0 border-r border-border overflow-y-auto py-2 [&>div>div]:hidden sm:[&>div>div]:block [&_button]:justify-center sm:[&_button]:justify-start [&_button]:min-h-10 sm:[&_button]:min-h-0 [&_button>span]:hidden sm:[&_button>span]:inline"
            groups={SETTINGS_GROUPS}
            tabs={allCategories}
            active={category}
            onSelect={setCategory}
          />

          <div className="flex-1 overflow-y-auto p-6 space-y-6 min-w-0">
            {tabContent(category, { tabs, loaded, setCategory, onCollectionsChanged })}
            {activePluginPage && <activePluginPage.Component />}
          </div>
        </div>

        <SettingsFooter form={form} version={tabs.version.version} />
      </div>
    </div>
  );
}

function SettingsFooter({
  form: { saveError, savedNotice, saving, save, onClose },
  version,
}: {
  form: ReturnType<typeof useSettingsForm>;
  version: string | null;
}) {
  return (
    <>
      {saveError && <p role="alert" className="border-t border-border px-3 py-2 text-xs text-danger">{saveError}</p>}
      {savedNotice && <p role="status" className="border-t border-border px-3 py-2 text-xs text-muted">Settings saved.</p>}
      <footer className="border-t border-border p-3 flex items-center justify-end gap-2 shrink-0">
        <span
          className="mr-auto text-[11px] text-muted"
          data-tooltip="Precursor version"
        >
          {version ? `Precursor v${version}` : ""}
        </span>
        <button
          onClick={() => void onClose()}
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
    </>
  );
}
