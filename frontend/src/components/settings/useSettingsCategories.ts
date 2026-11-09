import { useMemo, useState } from "react";
import { resolveSettingsPages } from "../../lib/plugins";
import { usePluginDescriptors } from "../../lib/pluginStore";
import { CATEGORIES, PLUGIN_TAB_PREFIX, type Category, type CategoryDef } from "./panelConfig";

/** The active settings tab, and the tab list with plugin pages appended. */
export function useSettingsCategories(initialCategory: Category | undefined) {
  const [category, setCategory] = useState<Category>(initialCategory ?? "appearance");
  // Settings pages contributed by installed plugins, appended as their own group.
  const pluginDescriptors = usePluginDescriptors();
  const pluginPages = useMemo(() => resolveSettingsPages(pluginDescriptors), [pluginDescriptors]);
  const allCategories = useMemo<ReadonlyArray<CategoryDef>>(
    () => [
      ...CATEGORIES,
      ...pluginPages.map((page) => ({
        id: `${PLUGIN_TAB_PREFIX}${page.id}`,
        label: page.label,
        icon: page.icon as CategoryDef["icon"],
        group: "Plugins" as const,
      })),
    ],
    [pluginPages],
  );
  const activePluginPage = useMemo(
    () => pluginPages.find((p) => `${PLUGIN_TAB_PREFIX}${p.id}` === category) ?? null,
    [pluginPages, category],
  );
  return { category, setCategory, allCategories, activePluginPage };
}
