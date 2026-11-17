import { call, httpError } from "./apiClient";
import { canvasRawUrl } from "./canvas";

// The canvas workspace's id never changes once made; ask once per page.
let workspaceId: Promise<number> | null = null;

export function canvasWorkspaceId(): Promise<number> {
  workspaceId ??= call("GET /api/canvas/workspace").then((ws) => ws.id);
  workspaceId.catch(() => {
    workspaceId = null;
  });
  return workspaceId;
}

/** A canvas's source text, fresh from disk (`version` busts the HTTP cache). */
export async function fetchCanvasSource(path: string, version: number): Promise<string> {
  const res = await fetch(canvasRawUrl(path, version));
  if (!res.ok) throw await httpError(res);
  return res.text();
}

export async function saveCanvasSource(path: string, content: string): Promise<void> {
  const id = await canvasWorkspaceId();
  await call("PUT /api/workspaces/{workspace_id}/file", {
    path: { workspace_id: id },
    query: { path },
    body: { content },
  });
}
