/**
 * Frontend plugin extension registry.
 *
 * Backend plugins describe contributions via `/api/plugins`; the SPA looks up
 * each `kind` + `slot` here. To support a new extension kind, register a React
 * component renderer once at boot and the matching backend extension will be
 * rendered wherever the slot is mounted.
 *
 * Designed as a stable seam — no extensions ship today; this layer exists so
 * future ones (drawio preview, mermaid renderer, ...) don't require core
 * changes.
 */

import type { ComponentType } from "react";
import type { PluginDescriptor } from "./types";

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
