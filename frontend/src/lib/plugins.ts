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
   * flow — core then hides the "+" affordance entirely.
   */
  newLabel?: string;
  /** Tailwind class tokens for the section's colour scheme. */
  colors: SectionColor;
  /** `--section-accent` in light / dark, injected as a stylesheet on register. */
  accent: { light: string; dark: string };
  /**
   * Gate the section on app state. A section whose backend package is installed
   * can still be unavailable — kanban, for one, needs a configured GitHub repo.
   * Defaults to always enabled.
   */
  isEnabled?: (ctx: SectionEnabledContext) => boolean;
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
 * (or whose registration's `isEnabled` says no) is dropped.
 */
export function resolveSections(
  descriptors: PluginDescriptor[] | null,
  ctx: SectionEnabledContext,
): SectionPlugin[] {
  if (!descriptors) return [];
  return descriptors
    .filter((d) => d.kind === SECTION_KIND && d.slot === SECTION_SLOT)
    .map((d) => ({ descriptor: d, section: sections.get(d.id) }))
    .filter(
      (x): x is { descriptor: PluginDescriptor; section: SectionPlugin } =>
        x.section != null && (x.section.isEnabled?.(ctx) ?? true),
    )
    .sort((a, b) => sectionOrder(a.descriptor) - sectionOrder(b.descriptor))
    .map((x) => x.section);
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
