import { X } from "lucide-react";
import { settingsStore } from "../lib/settingsStore";
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
import { SettingsPanelProps, SETTINGS_GROUPS } from "./settings/panelConfig";
import { useSettingsPanelState } from "./settings/useSettingsPanelState";
import { AppearanceTab } from "./settings/AppearanceTab";
import { ChatTab } from "./settings/ChatTab";
import { ModelTab } from "./settings/ModelTab";
import { GitHubTab } from "./settings/GitHubTab";
import { SpeechTab } from "./settings/SpeechTab";
import { LiveTab } from "./settings/LiveTab";
import { McpTab } from "./settings/McpTab";

export function SettingsPanel(props: SettingsPanelProps) {
  const s = useSettingsPanelState(props);
  const {
    onCollectionsChanged,
    category,
    setCategory,
    allCategories,
    activePluginPage,
    settings,
    setSettings,
    providers,
    modelFallbacks,
    setModelFallbacks,
    backupEnabled,
    setBackupEnabled,
    backupDir,
    setBackupDir,
    backupRetention,
    setBackupRetention,
    backupRunning,
    backupRunResult,
    sys,
    setSys,
    dockerAvailable,
    saving,
    saveError,
    saved,
    appVersion,
    save,
    runBackupNow,
    formSnapshot,
    baseline,
    onClose,
  } = s;
  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
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
            className="w-12 sm:w-52 shrink-0 border-r border-border overflow-y-auto py-2 [&>div>div]:hidden sm:[&>div>div]:block [&_button]:justify-center sm:[&_button]:justify-start [&_button]:min-h-10 sm:[&_button]:min-h-0 [&_button>span]:hidden sm:[&_button>span]:inline"
            groups={SETTINGS_GROUPS}
            tabs={allCategories}
            active={category}
            onSelect={setCategory}
          />

          <div className="flex-1 overflow-y-auto p-6 space-y-6 min-w-0">
            {category === "appearance" && <AppearanceTab s={s} />}



            {category === "chat" && <ChatTab s={s} />}

            {category === "presets" && (
              <PresetsSettings
                value={modelFallbacks}
                onChange={setModelFallbacks}
                settings={settings}
                providers={providers}
              />
            )}

            {category === "model" && <ModelTab s={s} />}

            {category === "github" && <GitHubTab s={s} />}

            {category === "speech" && <SpeechTab s={s} />}

            {category === "live" && <LiveTab s={s} />}

            {category === "iq" && (
              <IQSettings
                settings={settings}
                onSettings={(next) => {
                  setSettings(next);
                  settingsStore.set(next);
                }}
                providers={providers}
                onNavigate={setCategory}
              />
            )}

            {category === "mcp" && <McpTab s={s} />}

            {category === "skills" && <SkillsTab />}

            {category === "roles" && <RolesTab onOpenMigration={() => setCategory("definitions")} />}
            {category === "collections" && (
              <CollectionsTab onChanged={onCollectionsChanged} />
            )}

            {category === "memory" && <MemoriesTab />}
            {category === "agents" && <AgentsSettings />}
            {category === "plugins" && <PluginsSettings />}
            {activePluginPage && <activePluginPage.Component />}
            {category === "workflows" && (
              <WorkflowsSettings onOpenMigration={() => setCategory("definitions")} />
            )}
            {category === "definitions" && <DefinitionsMigrationWizard />}

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

        {saveError && <p role="alert" className="border-t border-border px-3 py-2 text-xs text-danger">{saveError}</p>}
        {saved && baseline.current === formSnapshot && <p role="status" className="border-t border-border px-3 py-2 text-xs text-muted">Settings saved.</p>}
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

