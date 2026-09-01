/**
 * What the board lists — the SPA mirror of `precursor_kanban.sources`.
 *
 * Two lists, stored in the plugin's settings blob and applied in order:
 * **sources** add boards beyond the configured repo's owner, then **hidden**
 * takes individual boards back out. The server drops anything it can't read, so
 * a bad entry is never fatal — but silently storing one the board then ignores
 * is a confusing way to find that out, so the same rules are enforced here
 * before anything is written.
 */

/** Mirrors `precursor_kanban.sources.SOURCES_KEY` / `HIDDEN_KEY`. */
export const SOURCES_KEY = "project_sources";
export const HIDDEN_KEY = "hidden_projects";

/** Mirrors `precursor_kanban.sources.MAX_SOURCES`. */
export const MAX_SOURCES = 20;

// Mirrors the regexes in `precursor_kanban.sources`.
const LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/;
const PROJECT_URL =
  /^https?:\/\/(?:www\.)?github\.com\/(?:orgs|users)\/([^/]+)\/projects\/\d+/i;
const OWNER_URL = /^https?:\/\/(?:www\.)?github\.com\/([^/?#]+)\/?$/i;

/** Whether the server will understand `raw` as a project source. */
export function isValidSource(raw: string): boolean {
  const text = raw.trim();
  if (!text) return false;
  const project = PROJECT_URL.exec(text);
  if (project) return LOGIN.test(project[1]);
  const owner = OWNER_URL.exec(text);
  if (owner) return LOGIN.test(owner[1]);
  if (text.includes("#")) {
    const [login, number] = text.split("#", 2);
    return LOGIN.test(login.trim()) && /^\d+$/.test(number.trim());
  }
  return LOGIN.test(text);
}

/**
 * Canonical `owner#number` identity for one board, matching
 * `precursor_kanban.sources.project_key`. Lower-cased, because GitHub logins
 * are case-insensitive and the two ends must agree on what "already hidden"
 * means.
 */
export function projectKey(owner: string, number: number): string {
  return `${owner.toLowerCase()}#${number}`;
}

/** Read a settings list defensively — the blob is whatever was last stored. */
export function readList(blob: Record<string, unknown>, key: string): string[] {
  const value = blob[key];
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}
