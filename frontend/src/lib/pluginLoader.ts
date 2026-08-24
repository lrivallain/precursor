/**
 * Load the frontend half of installed plugins at runtime.
 *
 * A plugin that ships a built bundle in its Python wheel advertises it as
 * `entry` on every descriptor it publishes. Before the SPA can resolve those
 * descriptors into sections it has to import those modules, because importing
 * one is what runs its `registerSection` call.
 *
 * The module resolves `react` / `@precursor/host` through the import map that
 * `vite.config.ts` injects, so it shares the host's React instance rather than
 * bringing its own — see `src/host/runtime.ts`.
 *
 * Plugins bundled into the core SPA (imported from `src/plugins/index.ts`) carry
 * no `entry` and are already registered by the time this runs; the two paths
 * converge on the same registry.
 */

import type { PluginDescriptor } from "./types";

/** Entry URLs already imported, so a re-fetch doesn't re-run a plugin's module. */
const loaded = new Set<string>();

/** Entry URLs that failed, with the reason, for diagnostics. */
const failures = new Map<string, string>();

export function pluginLoadFailures(): ReadonlyMap<string, string> {
  return failures;
}

/**
 * Import every distinct `entry` among `descriptors`.
 *
 * Resolves once all of them have settled. A plugin whose module throws is
 * recorded and skipped — a broken third-party bundle must not stop the app from
 * booting, and its descriptors simply resolve to nothing.
 */
export async function loadPluginEntries(
  descriptors: PluginDescriptor[],
): Promise<void> {
  const entries = [
    ...new Set(
      descriptors
        .map((d) => d.entry)
        .filter((e): e is string => typeof e === "string" && e.length > 0)
        .filter((e) => !loaded.has(e)),
    ),
  ];
  if (entries.length === 0) return;

  await Promise.all(
    entries.map(async (entry) => {
      try {
        // @vite-ignore: the URL is only known at runtime — it points at a file
        // served out of an installed Python package, not at anything in this
        // build graph.
        await import(/* @vite-ignore */ entry);
        loaded.add(entry);
        failures.delete(entry);
      } catch (e) {
        const reason = e instanceof Error ? e.message : String(e);
        failures.set(entry, reason);
        console.error(`Precursor: failed to load plugin bundle ${entry}`, e);
      }
    }),
  );
}
