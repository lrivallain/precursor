import { useEffect, useState } from "react";
import { api } from "./api";

/** The working copy's absolute path on the server, for "Open in VS Code". */
export function useWorkspaceRootPath(workspaceId: number): string | null {
  const [rootPath, setRootPath] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setRootPath(null);
    api.workspaces
      .localPath(workspaceId)
      .then(({ path }) => {
        if (!cancelled) setRootPath(path);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);
  return rootPath;
}
