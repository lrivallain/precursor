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
  /** Account that owns the board — boards can come from several. */
  owner: string | null;
  /**
   * Where the board came from. `repo` is the implicit default (the configured
   * repository's owner) and has no settings entry behind it, so it can be
   * hidden but not "stopped tracking"; the other two were added explicitly.
   */
  source: "repo" | "account" | "pinned";
  /** The settings entry that produced it, verbatim. `null` when `repo`. */
  source_ref: string | null;
  /**
   * Whether the user has hidden this board. Hidden boards are still returned —
   * the picker is the only place to unhide one, so omitting them would make
   * hiding a one-way door.
   */
  hidden: boolean;
}

/**
 * A configured source that currently yields no boards — broken (renamed,
 * revoked, made private) or genuinely empty. Surfaced so the entry stays
 * visible and removable instead of being invisible *and* undeletable.
 */
export interface UnresolvedSource {
  /** The stored entry, verbatim, for an exact removal. */
  ref: string;
  kind: "account" | "pinned";
}

/** What `GET /api/github/projects` returns. */
export interface ProjectListing {
  projects: ProjectSummary[];
  unresolved: UnresolvedSource[];
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
