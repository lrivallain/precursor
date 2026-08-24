/**
 * The kanban plugin's own HTTP client, backed by core's shared `request`
 * helper (auth headers, error unwrapping). Core's `api` object stays free of
 * Projects v2 endpoints — they only exist when the plugin is installed.
 */

import { request } from "@precursor/host";
import type { ItemStatusResult, ProjectBoard, ProjectSummary } from "./types";

export const kanbanApi = {
  listProjects: (repo?: string) => {
    const qs = repo ? `?repo=${encodeURIComponent(repo)}` : "";
    return request<ProjectSummary[]>(`/api/github/projects${qs}`);
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
