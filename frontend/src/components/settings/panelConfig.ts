import { Palette, MessageSquare, Cpu, Plug, Puzzle, Radio, Sparkles, Drama, Brain, SlidersHorizontal, Workflow as WorkflowIcon, FileCode2, Mic, BarChart3, Bot, HardDriveDownload, Layers, ListOrdered } from "lucide-react";
import { GithubIcon as Github } from "../icons/GithubIcon";
import { type FontChoice } from "../../lib/font";

export interface SettingsPanelProps {
  onClose: () => void;
  /** Opens straight to a category (e.g. "collections" from the switcher). */
  initialCategory?: Category;
  /** Fired when collections change so the sidebar switcher can reload. */
  onCollectionsChanged?: () => void | Promise<void>;
}

/**
 * A settings tab. Core's own are enumerated; a plugin contributes its page id,
 * which is why this stays open. `(string & {})` keeps completion for the core
 * names while accepting any plugin id.
 */
export type Category = CoreCategory | (string & {});

export type CoreCategory =
  | "appearance"
  | "chat"
  | "model"
  | "presets"
  | "github"
  | "speech"
  | "live"
  | "mcp"
  | "iq"
  | "skills"
  | "roles"
  | "collections"
  | "memory"
  | "agents"
  | "workflows"
  | "definitions"
  | "plugins"
  | "stats"
  | "backup"
  | "system";

export type SettingsGroup = "App" | "Integrations" | "Extensions" | "Plugins" | "Advanced";

export interface CategoryDef {
  id: Category;
  label: string;
  icon: typeof Palette;
  group: SettingsGroup;
}

// Plugin pages are addressed with a prefix so a plugin id can never collide
// with a core category. Without it, a plugin called "github" would produce two
// tabs sharing an id — both rendering as active, and both panels stacked in the
// content area — and the widened `Category` type would not catch it.
export const PLUGIN_TAB_PREFIX = "plugin:";

/** Settings tab id for a plugin's page. The one place that encodes the scheme. */
export function pluginSettingsTab(pageId: string): string {
  return `${PLUGIN_TAB_PREFIX}${pageId}`;
}

// Groups in nav order. "Plugins" is declared here but populated at runtime from
// whatever plugins publish a settings page; `SidebarTabs` hides it when empty.
export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  "App",
  "Integrations",
  "Extensions",
  "Plugins",
  "Advanced",
];

export const CATEGORIES: ReadonlyArray<CategoryDef> = [
  { id: "appearance", label: "Appearance", icon: Palette, group: "App" },
  { id: "chat", label: "Chat", icon: MessageSquare, group: "App" },
  { id: "model", label: "Model", icon: Cpu, group: "App" },
  { id: "presets", label: "Presets", icon: ListOrdered, group: "App" },
  { id: "github", label: "GitHub", icon: Github, group: "Integrations" },
  { id: "speech", label: "Speech-to-text", icon: Mic, group: "Integrations" },
  { id: "live", label: "Live", icon: Radio, group: "Integrations" },
  { id: "mcp", label: "MCP servers", icon: Plug, group: "Integrations" },
  { id: "iq", label: "Precursor IQ", icon: Sparkles, group: "Integrations" },
  { id: "skills", label: "Skills", icon: Sparkles, group: "Extensions" },
  { id: "roles", label: "Roles", icon: Drama, group: "Extensions" },
  { id: "collections", label: "Collections", icon: Layers, group: "Extensions" },
  { id: "memory", label: "Memory", icon: Brain, group: "Extensions" },
  { id: "agents", label: "Agents", icon: Bot, group: "Extensions" },
  { id: "workflows", label: "Workflows", icon: WorkflowIcon, group: "Extensions" },
  { id: "definitions", label: "Definition files", icon: FileCode2, group: "Extensions" },
  { id: "plugins", label: "Plugins", icon: Puzzle, group: "Extensions" },
  { id: "stats", label: "Usage stats", icon: BarChart3, group: "Advanced" },
  { id: "backup", label: "Backup", icon: HardDriveDownload, group: "Advanced" },
  { id: "system", label: "System", icon: SlidersHorizontal, group: "Advanced" },
];

// Speech-to-text recognition languages (BCP-47). "" => use the browser locale.
export const STT_LANGUAGES: ReadonlyArray<{ value: string; label: string }> = [
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

// CSS font stacks for the picker's own label previews, keyed by FontChoice.
// "system" is left undefined so the label just inherits the app's actual font
// (whatever `--font-sans` currently resolves to) rather than hardcoding Inter.
export const FONT_PREVIEW_STACKS: Record<FontChoice, string | undefined> = {
  system: undefined,
  opendyslexic: '"OpenDyslexic", sans-serif',
  "atkinson-hyperlegible": '"Atkinson Hyperlegible", sans-serif',
  lexend: '"Lexend", sans-serif',
};
