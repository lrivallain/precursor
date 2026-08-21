/**
 * Deep links to a workspace file, surfaced from an MCP tool result.
 *
 * The file-writing MCP servers (`drawio`, `workspace-fs`) annotate a successful
 * write with `workspace_slug` + `url` (see `services/mcp/workspace_links.py`).
 * `ToolCallBubble` reads that here to offer an "Open" chip, so a diagram or file
 * the assistant just wrote during a conversation is one click from the Files
 * section instead of something you have to go find.
 */

const OPEN_WORKSPACE_FILE_EVENT = "precursor:open-workspace-file";

export interface WorkspaceFileLink {
  slug: string;
  path: string;
  /** Basename, for the chip label. */
  name: string;
  isDiagram: boolean;
}

function isDiagramPath(path: string): boolean {
  const lower = path.toLowerCase();
  return lower.endsWith(".drawio") || lower.endsWith(".drawio.xml");
}

/**
 * Pull a workspace file link out of a tool result body, or null when the result
 * isn't a successful workspace write. Tolerates non-JSON and unrelated tools:
 * every field is validated rather than assumed.
 */
export function parseWorkspaceFileLink(
  content: string | null | undefined,
): WorkspaceFileLink | null {
  if (!content) return null;
  const trimmed = content.trim();
  if (!trimmed.startsWith("{")) return null;

  let body: Record<string, unknown>;
  try {
    body = JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null) return null;
  // A result carrying an `error` never links anywhere, even if the server also
  // echoed back a path.
  if (body.error) return null;

  const slug = body.workspace_slug;
  const path = body.path;
  if (typeof slug !== "string" || !slug) return null;
  if (typeof path !== "string" || !path) return null;
  // Never offer a chip we can't turn into a safe route.
  if (workspaceFileUrl(slug, path) === null) return null;

  const name = path.split("/").filter(Boolean).pop() ?? path;
  return { slug, path, name, isDiagram: isDiagramPath(path) };
}

/** Segments a browser would resolve away, changing where the link points. */
const UNSAFE_SEGMENTS = new Set([".", ".."]);

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
