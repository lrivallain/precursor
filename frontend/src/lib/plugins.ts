/**
 * Frontend plugin registry.
 *
 * Backend plugins describe their contributions via `/api/plugins`; the SPA
 * looks each one up here. Two contracts live side by side:
 *
 * - **Sections** (`kind: "section"`) — a plugin owns a whole surface: an entry
 *   in the sidebar rail, a card on the home screen, a command-palette entry and
 *   a top-level route. Core renders the section's own components and knows
 *   nothing else about it. `frontend/src/plugins/kanban` is the reference
 *   implementation.
 * - **Extensions** (any other `kind`) — a component mounted into a named slot.
 *
 * A descriptor and a registration must agree on `id`: the backend decides
 * whether a section exists at all (it only appears if its Python package is
 * installed), while the frontend half supplies the icon, palette and React
 * components. Registering without a matching descriptor renders nothing.
 */

import type { ComponentType, ReactNode } from "react";
import type { PluginDescriptor, Settings } from "./types";
import type { SectionColor } from "./sections";

/* -------------------------------------------------------------------------- */
/* Slot extensions                                                             */
/* -------------------------------------------------------------------------- */

export type ExtensionProps = {
  descriptor: PluginDescriptor;
};

type Renderer = ComponentType<ExtensionProps>;

const renderers = new Map<string, Renderer>();

export function registerRenderer(kind: string, renderer: Renderer): void {
  renderers.set(kind, renderer);
}

export function getRenderer(kind: string): Renderer | undefined {
  return renderers.get(kind);
}

export function pluginsForSlot(
  descriptors: PluginDescriptor[],
  slot: string,
): PluginDescriptor[] {
  return descriptors.filter((d) => d.slot === slot);
}

/* -------------------------------------------------------------------------- */
/* Sections                                                                    */
/* -------------------------------------------------------------------------- */

/** Descriptor `kind` + `slot` a backend section contributes. */
export const SECTION_KIND = "section";
export const SECTION_SLOT = "app.section";

/** Descriptor `kind` + `slot` for a plugin's page in the Settings modal. */
export const SETTINGS_PAGE_KIND = "settings-page";
export const SETTINGS_PAGE_SLOT = "settings.tabs";

/** What a section can inspect to decide whether it should be available. */
export interface SectionEnabledContext {
  /** App settings, or `null` while they load. */
  settings: Settings | null;
}

/**
 * The host services a section gets. Everything a plugin needs from core goes
 * through here, so the two never reach into each other's internals.
 */
export interface SectionHost {
  /** Path segments *after* the section root: `/kanban/4-board` → `["4-board"]`. */
  segments: string[];
  /** Current URL hash, without the leading `#` (empty when absent). */
  hash: string;
  /**
   * Rewrite the section-relative URL. `push` adds a history entry (a genuine
   * navigation); the default replaces it, for state the user didn't explicitly
   * navigate to.
   */
  navigate: (segments: string[], hash?: string, opts?: { push?: boolean }) => void;
  /** Leave the section and open a Precursor topic. */
  openTopic: (topicId: number) => void;
  /**
   * Open the Settings modal, on a plugin's own page when one is named.
   *
   * Pass the settings-page id (usually the plugin's own); the host handles
   * addressing it, so a plugin never has to know how tabs are keyed. With no
   * argument it opens the Plugins list.
   */
  openSettings: (pluginPageId?: string) => void;
  /** App settings, or `null` while they load. */
  settings: Settings | null;
}

export interface SectionIconProps {
  size?: number;
  className?: string;
}

export interface SectionPlugin {
  /**
   * Must match the backend descriptor's `id`. It doubles as the section's
   * top-level URL segment (`/<id>`), so keep it URL-safe.
   */
  id: string;
  /** Sidebar rail + home card label. */
  label: string;
  icon: ComponentType<SectionIconProps>;
  /** Home card blurb. */
  description: string;
  /** Home card call-to-action, e.g. "Open board". */
  openLabel: string;
  /** Extra command-palette search terms. */
  keywords?: string;
  /**
   * Label for the header "New …" action. Omit for a section with no create
   * flow — core then hides the "+" affordance entirely. Pair it with `onNew`,
   * which is what the button actually does.
   */
  newLabel?: string;
  /**
   * Run the section's "New …" action. Core owns the button (so every section's
   * sits in the same place and wears the section's own tint) and the section
   * owns what it means.
   */
  onNew?: (host: SectionHost) => void;
  /** Tailwind class tokens for the section's colour scheme. */
  colors: SectionColor;
  /** `--section-accent` in light / dark, injected as a stylesheet on register. */
  accent: { light: string; dark: string };
  /**
   * Gate the section on app state, and say *why* when it's closed.
   *
   * Return `null` when the section applies, or a short sentence explaining what
   * is missing — kanban, for one, needs a configured GitHub repo. The reason is
   * shown in Settings → Plugins, because a plugin that is installed and enabled
   * yet nowhere to be seen is otherwise indistinguishable from a broken one.
   *
   * Omit it for a section that always applies.
   */
  unavailable?: (ctx: SectionEnabledContext) => string | null;
  /**
   * Optional wrapper mounted around the whole app shell while the section is
   * active. Sections whose sidebar and main panes share state put their context
   * provider here — the two are rendered into different subtrees.
   */
  Provider?: ComponentType<{ host: SectionHost; children: ReactNode }>;
  /** Rendered in the sidebar body (the section's own list / picker). */
  Sidebar: ComponentType<{ host: SectionHost }>;
  /** Rendered as the main content pane. */
  Main: ComponentType<{ host: SectionHost }>;
  /** Rendered as the header title; falls back to `label`. */
  Title?: ComponentType<{ host: SectionHost }>;
}

const sections = new Map<string, SectionPlugin>();

// `resolveSections` runs on every settings/descriptor change, so the warning
// below has to fire once per section rather than once per render.
const warned = new Set<string>();

function warnOnce(key: string, message: string): void {
  if (warned.has(key)) return;
  warned.add(key);
  console.warn(message);
}

/**
 * Register a section implementation. Call once at module scope from the
 * plugin's entry file; `frontend/src/plugins/index.ts` imports them all.
 */
export function registerSection(section: SectionPlugin): void {
  sections.set(section.id, section);
  injectSectionAccent(section);
}

export function getSection(id: string): SectionPlugin | undefined {
  return sections.get(id);
}

/**
 * Resolve the descriptors the backend published into registered sections,
 * ordered by the backend's `config.order`. A descriptor with no registration
 * (or whose registration reports itself unavailable) is dropped.
 */
export function resolveSections(
  descriptors: PluginDescriptor[] | null,
  ctx: SectionEnabledContext,
): SectionPlugin[] {
  if (!descriptors) return [];
  return descriptors
    .filter((d) => d.kind === SECTION_KIND && d.slot === SECTION_SLOT)
    .map((d) => ({ descriptor: d, section: sections.get(d.id) }))
    .filter((x) => {
      if (x.section == null) {
        // The backend advertised a section nothing registered — almost always a
        // plugin whose frontend bundle is missing from its package. Say so here
        // too; Settings → Plugins explains it, but the console is where someone
        // debugging a vanished section usually looks first.
        warnOnce(
          x.descriptor.id,
          `Precursor: plugin "${x.descriptor.plugin_id}" advertises the section ` +
            `"${x.descriptor.id}" but no implementation is registered. Its frontend ` +
            `bundle is probably missing (build it with \`make plugins-build\`, or ` +
            `reinstall the package).`,
        );
        return false;
      }
      return sectionUnavailableReason(x.section, ctx) === null;
    })
    .filter(
      (x): x is { descriptor: PluginDescriptor; section: SectionPlugin } =>
        x.section != null,
    )
    .sort((a, b) => sectionOrder(a.descriptor) - sectionOrder(b.descriptor))
    .map((x) => x.section);
}

/**
 * Why `section` isn't currently showing, or `null` when it is.
 *
 * Settings → Plugins uses this to explain an enabled plugin whose section is
 * nowhere to be seen, which otherwise looks exactly like a broken toggle.
 */
export function sectionUnavailableReason(
  section: SectionPlugin,
  ctx: SectionEnabledContext,
): string | null {
  return section.unavailable?.(ctx) ?? null;
}

function sectionOrder(descriptor: PluginDescriptor): number {
  const order = descriptor.config?.order;
  return typeof order === "number" ? order : 100;
}

/**
 * Publish `--section-accent` for a section as a real stylesheet rule.
 *
 * The variable has to resolve differently under `.dark`, which an inline style
 * can't express, and Tailwind only emits class names it can find in source — so
 * a plugin's accent is injected once at registration instead.
 */
function injectSectionAccent(section: SectionPlugin): void {
  if (typeof document === "undefined") return;
  const id = `section-accent-${section.id}`;
  if (document.getElementById(id)) return;
  const style = document.createElement("style");
  style.id = id;
  style.textContent =
    `.section-${section.id} { --section-accent: ${section.accent.light}; }\n` +
    `.dark .section-${section.id} { --section-accent: ${section.accent.dark}; }`;
  document.head.append(style);
}

/* -------------------------------------------------------------------------- */
/* Settings pages                                                             */
/* -------------------------------------------------------------------------- */

/**
 * A plugin's own page in the Settings modal, listed under a "Plugins" group.
 *
 * Its values are stored as one opaque JSON blob per plugin
 * (`/api/plugins/installed/<id>/settings`), so a plugin never has to add fields
 * to core's settings schema — and two plugins can't collide.
 */
export interface SettingsPagePlugin {
  /** Must match the backend descriptor's id; also the storage namespace. */
  id: string;
  label: string;
  icon: ComponentType<SectionIconProps>;
  /** The panel body. Owns its own loading, saving and validation. */
  Component: ComponentType<Record<string, never>>;
}

const settingsPages = new Map<string, SettingsPagePlugin>();

/** Register a settings panel. Call at module scope from the plugin's entry. */
export function registerSettingsPage(page: SettingsPagePlugin): void {
  settingsPages.set(page.id, page);
}

export function getSettingsPage(id: string): SettingsPagePlugin | undefined {
  return settingsPages.get(id);
}

/**
 * Settings pages the backend published *and* the frontend registered, ordered
 * by the backend's `config.order`.
 *
 * Unlike sections these aren't gated on app state: a plugin whose configuration
 * is incomplete is exactly the plugin whose settings you need to reach.
 */
export function resolveSettingsPages(
  descriptors: PluginDescriptor[] | null,
): SettingsPagePlugin[] {
  if (!descriptors) return [];
  return descriptors
    .filter((d) => d.kind === SETTINGS_PAGE_KIND && d.slot === SETTINGS_PAGE_SLOT)
    .map((d) => ({ descriptor: d, page: settingsPages.get(d.id) }))
    .filter(
      (x): x is { descriptor: PluginDescriptor; page: SettingsPagePlugin } =>
        x.page != null,
    )
    .sort((a, b) => sectionOrder(a.descriptor) - sectionOrder(b.descriptor))
    .map((x) => x.page);
}
