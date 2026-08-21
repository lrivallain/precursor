/**
 * Deep links to a workspace file, surfaced from an MCP tool call.
 *
 * The file MCP servers (`drawio`, `workspace-fs`) annotate a successful read or
 * write with the workspace + path (see `services/mcp/workspace_links.py`). The
 * backend lifts that into the tool call's *metadata* — deliberately not read out
 * of the result body, which for a read embeds the whole file (up to 2 MB for a
 * diagram) and would otherwise have to be JSON-parsed on every render just to
 * find a path.
 *
 * `ToolCallBubble` turns it into an "Open" chip, so a diagram or file the
 * assistant just touched is one click from the Files section.
 */

/** Segments a browser would resolve away, changing where the link points. */
const UNSAFE_SEGMENTS = new Set([".", ".."]);

const OPEN_WORKSPACE_FILE_EVENT = "precursor:open-workspace-file";

/** The `link` carried on a tool call's metadata: `{slug, path}`. */
export interface WorkspaceFileRef {
  slug: string;
  path: string;
}

export interface WorkspaceFileLink extends WorkspaceFileRef {
  /** Basename, for the chip label. */
  name: string;
  isDiagram: boolean;
}

function isDiagramPath(path: string): boolean {
  const lower = path.toLowerCase();
  return lower.endsWith(".drawio") || lower.endsWith(".drawio.xml");
}

/**
 * Build the SPA route for a workspace file, or null when the path wouldn't
 * survive URL resolution. Mirrors `workspace_links.py`.
 */
export function workspaceFileUrl(slug: string, path: string): string | null {
  if (!slug) return null;
  const segments = path.split("/").filter(Boolean);
  if (segments.length === 0) return null;
  if (segments.some((seg) => UNSAFE_SEGMENTS.has(seg))) return null;
  const encoded = segments.map((seg) => encodeURIComponent(seg)).join("/");
  return `/ws/${encodeURIComponent(slug)}/${encoded}`;
}

/**
 * Validate a `link` from tool metadata and derive what the chip needs. Returns
 * null for anything that isn't a usable workspace file reference — the metadata
 * ultimately originates from an MCP server, which may be third-party.
 */
export function toWorkspaceFileLink(
  ref: WorkspaceFileRef | null | undefined,
): WorkspaceFileLink | null {
  if (!ref) return null;
  const { slug, path } = ref;
  if (typeof slug !== "string" || typeof path !== "string") return null;
  if (workspaceFileUrl(slug, path) === null) return null;
  const name = path.split("/").filter(Boolean).pop() ?? path;
  return { slug, path, name, isDiagram: isDiagramPath(path) };
}

/** Switch the app to the Files section with this file open. */
export function openWorkspaceFile(slug: string, path: string): void {
  window.dispatchEvent(
    new CustomEvent(OPEN_WORKSPACE_FILE_EVENT, { detail: { slug, path } }),
  );
}

export function subscribeOpenWorkspaceFile(
  handler: (detail: { slug: string; path: string }) => void,
): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<{ slug: string; path: string }>).detail;
    if (detail?.slug && detail?.path) handler(detail);
  };
  window.addEventListener(OPEN_WORKSPACE_FILE_EVENT, listener);
  return () => window.removeEventListener(OPEN_WORKSPACE_FILE_EVENT, listener);
}
