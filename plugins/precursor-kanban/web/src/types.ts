/**
 * GitHub Projects v2 board types — the SPA mirror of
 * `precursor_kanban.schemas`. They live with the plugin rather than in
 * `lib/types.ts` so removing the plugin removes them too.
 */

import type { IssueLabel } from "@precursor/host";

export interface ProjectSummary {
  id: string;
  number: number;
  title: string;
  url: string | null;
  closed: boolean;
  short_description: string | null;
}

export interface ProjectColumn {
  id: string;
  name: string;
}

export interface ProjectStatusField {
  id: string;
  name: string;
  options: ProjectColumn[];
}

export interface ProjectCard {
  // ProjectV2 item id — the handle used for status mutations.
  id: string;
  type: "issue" | "pull_request";
  number: number | null;
  title: string;
  url: string | null;
  state: string | null;
  // owner/name of the item's source repo (ProjectsV2 can span repos).
  repo: string | null;
  status_option_id: string | null;
  status_name: string | null;
  labels: IssueLabel[];
}

export interface ProjectBoard {
  id: string;
  title: string;
  url: string | null;
  status_field: ProjectStatusField | null;
  items: ProjectCard[];
}

export interface ItemStatusResult {
  item_id: string;
  option_id: string;
}
