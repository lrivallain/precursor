/** The folders that contain `path`, outermost first: "a/b/c.md" → ["a", "a/b"]. */
export function ancestorDirs(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
}

/**
 * `collapsed` with every folder holding `path` opened, so the file shows in
 * the tree as if it had been browsed to. Returns the same set when nothing
 * changes, so a React state update is a no-op.
 */
export function revealPath(collapsed: Set<string>, path: string): Set<string> {
  const closed = ancestorDirs(path).filter((dir) => collapsed.has(dir));
  if (closed.length === 0) return collapsed;
  const next = new Set(collapsed);
  closed.forEach((dir) => next.delete(dir));
  return next;
}
