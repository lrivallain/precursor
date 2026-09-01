/**
 * The kanban plugin's own HTTP client, backed by core's shared `request`
 * helper (auth headers, error unwrapping). Core's `api` object stays free of
 * Projects v2 endpoints — they only exist when the plugin is installed.
 */

import { api, request } from "@precursor/host";
import { HIDDEN_KEY, MAX_SOURCES, SOURCES_KEY, readList } from "./sources";
import type { ItemStatusResult, ProjectBoard, ProjectListing } from "./types";

/** Settings namespace — the plugin id, mirroring `plugin.SECTION_ID`. */
const PLUGIN_ID = "kanban";

/**
 * Edit one list inside the plugin's settings blob.
 *
 * Re-reads before writing rather than PUTting a copy the caller has been
 * holding, so two board actions in quick succession compose instead of the
 * second undoing the first. Everything else in the blob is passed through
 * untouched, since the server replaces the document wholesale.
 *
 * This does *not* defend against the settings panel, which holds a whole-blob
 * draft from mount and PUTs it on Save. Nothing needs it to: that panel lives
 * in a modal over the board, so its draft can't be open while these actions are
 * reachable.
 */
async function editList(
  key: string,
  update: (current: string[]) => string[],
): Promise<void> {
  const blob = await api.plugins.settings.get(PLUGIN_ID);
  await api.plugins.settings.put(PLUGIN_ID, { ...blob, [key]: update(readList(blob, key)) });
}

export const kanbanApi = {
  listProjects: (repo?: string) => {
    const qs = repo ? `?repo=${encodeURIComponent(repo)}` : "";
    return request<ProjectListing>(`/api/github/projects${qs}`);
  },

  board: (projectId: string) =>
    request<ProjectBoard>(`/api/github/projects/${encodeURIComponent(projectId)}/board`),

  setItemStatus: (
    projectId: string,
    itemId: string,
    data: { field_id: string; option_id: string },
  ) =>
    request<ItemStatusResult>(
      `/api/github/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(
        itemId,
      )}/status`,
      { method: "POST", body: JSON.stringify(data) },
    ),
};

/**
 * Mutations against the plugin's settings blob, phrased as the board's actions
 * rather than as document edits — "hide this board", not "append to a list".
 */
export const kanbanSettings = {
  read: () => api.plugins.settings.get(PLUGIN_ID),

  /** Track another account or project. Adding a duplicate is a no-op. */
  addSource: (entry: string) =>
    editList(SOURCES_KEY, (sources) =>
      sources.includes(entry) || sources.length >= MAX_SOURCES
        ? sources
        : [...sources, entry],
    ),

  /**
   * Stop tracking a source. Matched verbatim against the stored entry, which is
   * why the server hands back `source_ref` as typed rather than normalised.
   */
  removeSource: (entry: string) =>
    editList(SOURCES_KEY, (sources) => sources.filter((s) => s !== entry)),

  /** Hide one board, by its canonical `owner#number` key. */
  hideProject: (key: string) =>
    editList(HIDDEN_KEY, (hidden) => (hidden.includes(key) ? hidden : [...hidden, key])),

  /** Unhide one board, matched verbatim against the stored entry. */
  unhideProject: (entry: string) =>
    editList(HIDDEN_KEY, (hidden) => hidden.filter((e) => e !== entry)),
};
