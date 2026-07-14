import { useCallback, useEffect, useRef, useState } from "react";
import {
  ClipboardCheck,
  ClipboardCopy,
  Download,
  ExternalLink,
  Eye,
  FilePlus2,
  FileText,
  FolderOpen,
  FolderPlus,
  GitBranch,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Trash2,
  Upload,
} from "lucide-react";
import { api, workspaceRawUrl } from "../lib/api";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useConfirm } from "./ConfirmDialog";
import { Markdown } from "./Markdown";
import { ResizeHandle } from "./ResizeHandle";
import { WorkspaceChat } from "./WorkspaceChat";
import { FileTree } from "./FileTree";
import { ChangesModal } from "./GitDiffViewer";
import type {
  GitActionResult,
  GitStatus,
  Workspace,
  WorkspaceFileNode,
} from "../lib/types";

const TEXT_EXTS = [
  ".md",
  ".markdown",
  ".txt",
  ".rst",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
  ".csv",
  ".html",
  ".css",
  ".js",
  ".ts",
  ".py",
  ".sh",
];

function isEditable(name: string): boolean {
  const lower = name.toLowerCase();
  return TEXT_EXTS.some((e) => lower.endsWith(e)) || lower.startsWith(".");
}

function isMarkdown(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".md") || lower.endsWith(".markdown");
}

function isHtml(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".html") || lower.endsWith(".htm");
}

// --------------------------------------------------------------------------
// Workspace: file tree + editor + chat for one workspace
// --------------------------------------------------------------------------

export function WorkspaceView({
  workspace,
  initialPath,
  onPathChange,
  onDeleted,
  onSetRole,
  onOpenRoleSelector,
}: {
  workspace: Workspace;
  initialPath: string | null;
  onPathChange: (path: string | null) => void;
  onDeleted: () => void;
  onSetRole?: (roleId: number | null) => Promise<void>;
  onOpenRoleSelector?: () => void;
}) {
  const confirmAction = useConfirm();
  const area = workspace;
  const [files, setFiles] = useState<WorkspaceFileNode[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [mode, setMode] = useState<"edit" | "preview">("preview");
  const [loadingFile, setLoadingFile] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedPath, setCopiedPath] = useState(false);
  // Inline create-in-tree state (VS Code style): an input row appears at the
  // target parent ("" = root) until the user confirms or cancels. No modal.
  const [pendingCreate, setPendingCreate] = useState<{
    kind: "file" | "folder";
    parent: string;
  } | null>(null);

  const dirty = content !== savedContent;
  const isGit = area.kind !== "local";

  // Resizable Files panel (left). Width persists per browser.
  const { width: filesWidth, onMouseDown: onFilesResize } = useResizableWidth({
    storageKey: "precursor:workspace:filesWidth",
    defaultWidth: 240,
    min: 160,
    max: 520,
  });

  const refreshFiles = useCallback(async () => {
    setFiles(await api.workspaces.listFiles(area.id));
  }, [area.id]);

  const refreshStatus = useCallback(async () => {
    if (area.kind === "local") {
      setStatus(null);
      return;
    }
    try {
      setStatus(await api.workspaces.gitStatus(area.id));
    } catch {
      setStatus(null);
    }
  }, [area.id, area.kind]);

  useEffect(() => {
    void refreshFiles();
    void refreshStatus();
  }, [refreshFiles, refreshStatus]);

  // Open the file named in the URL once the tree has loaded.
  const didInitialOpen = useRef(false);
  useEffect(() => {
    if (didInitialOpen.current || !initialPath || files.length === 0) return;
    if (files.some((f) => f.path === initialPath && f.type !== "dir")) {
      didInitialOpen.current = true;
      void openFile(initialPath);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files, initialPath]);

  async function openFile(path: string): Promise<void> {
    if (
      dirty &&
      !(await confirmAction({
        message: "Discard unsaved changes?",
        confirmLabel: "Discard changes",
        variant: "warning",
      }))
    )
      return;
    setLoadingFile(true);
    setError(null);
    try {
      const f = await api.workspaces.readFile(area.id, path);
      setActivePath(path);
      onPathChange(path);
      setContent(f.content);
      setSavedContent(f.content);
      setMode(isMarkdown(path) || isHtml(path) ? "preview" : "edit");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingFile(false);
    }
  }

  async function save(): Promise<void> {
    if (!activePath) return;
    setSaving(true);
    setError(null);
    try {
      await api.workspaces.writeFile(area.id, activePath, content);
      setSavedContent(content);
      await refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function applyPathChange(oldPath: string, newPath: string): Promise<void> {
    if (!newPath || newPath === oldPath) return;
    setError(null);
    try {
      await api.workspaces.renameEntry(area.id, oldPath, newPath);
      await refreshFiles();
      await refreshStatus();
      // Keep the editor pointed at the moved/renamed file (or a file inside a
      // moved/renamed folder), preserving any unsaved buffer.
      if (activePath === oldPath) {
        setActivePath(newPath);
        onPathChange(newPath);
      } else if (activePath && activePath.startsWith(`${oldPath}/`)) {
        const moved = newPath + activePath.slice(oldPath.length);
        setActivePath(moved);
        onPathChange(moved);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      throw e;
    }
  }

  async function handleRename(oldPath: string, newName: string): Promise<void> {
    const cleaned = newName.trim().replace(/^\/+|\/+$/g, "").replace(/\/{2,}/g, "/");
    const slash = oldPath.lastIndexOf("/");
    const parent = slash === -1 ? "" : oldPath.slice(0, slash);
    const newPath = parent ? `${parent}/${cleaned}` : cleaned;
    if (!cleaned) return;
    await applyPathChange(oldPath, newPath);
  }

  async function handleMove(src: string, targetDir: string): Promise<void> {
    const name = src.slice(src.lastIndexOf("/") + 1);
    const newPath = targetDir ? `${targetDir}/${name}` : name;
    await applyPathChange(src, newPath);
  }

  async function submitCreate(name: string): Promise<void> {
    if (!pendingCreate) return;
    const cleaned = name.trim().replace(/^\/+|\/+$/g, "").replace(/\/{2,}/g, "/");
    if (!cleaned) {
      setPendingCreate(null);
      return;
    }
    const full = pendingCreate.parent ? `${pendingCreate.parent}/${cleaned}` : cleaned;
    setError(null);
    try {
      if (pendingCreate.kind === "file") {
        await api.workspaces.createFile(area.id, full, "");
        await refreshFiles();
        await openFile(full);
      } else {
        await api.workspaces.createFolder(area.id, full);
        await refreshFiles();
      }
      setPendingCreate(null);
    } catch (e) {
      // Keep the input open so the user can correct the name (e.g. a conflict).
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function copyFilePath(path: string): Promise<void> {
    setError(null);
    try {
      const { path: root } = await api.workspaces.localPath(area.id);
      const full = `${root.replace(/\/+$/, "")}/${path}`;
      await navigator.clipboard.writeText(full);
      setCopiedPath(true);
      window.setTimeout(() => setCopiedPath(false), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function deleteFile(path: string): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete "${path}"? It will be removed on the next push.`,
        confirmLabel: "Delete file",
        variant: "danger",
      }))
    )
      return;
    setError(null);
    try {
      await api.workspaces.deleteFile(area.id, path);
      if (activePath === path) {
        setActivePath(null);
        onPathChange(null);
        setContent("");
        setSavedContent("");
      }
      await refreshFiles();
      await refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="h-full flex flex-col">
      {isGit ? (
        <GitBar
          area={area}
          status={status}
          dirty={dirty}
          onRefreshStatus={refreshStatus}
          onAfterSync={async () => {
            await refreshFiles();
            await refreshStatus();
            // Reload the open file in case it changed during a pull.
            if (activePath) {
              try {
                const f = await api.workspaces.readFile(area.id, activePath);
                setContent(f.content);
                setSavedContent(f.content);
              } catch {
                setActivePath(null);
              }
            }
          }}
          onDeleted={onDeleted}
          onError={setError}
        />
      ) : (
        <LocalWorkspaceBar area={area} onDeleted={onDeleted} onError={setError} />
      )}

      {error && (
        <div className="px-4 py-2 text-sm bg-red-500/10 text-red-500 border-b border-border">
          {error}
        </div>
      )}

      <div className="flex-1 min-h-0 flex">
        <aside
          className="relative shrink-0 border-r border-border flex flex-col min-h-0"
          style={{ width: filesWidth }}
        >
          <ResizeHandle onMouseDown={onFilesResize} side="right" />
          <div className="flex items-center justify-between px-3 h-10 border-b border-border">
            <span className="text-xs font-medium text-muted uppercase tracking-wide">
              Files
            </span>
            <div className="flex items-center gap-0.5">
              <button
                className="p-1 rounded hover:bg-surface text-muted hover:text-text"
                aria-label="New folder"
                data-tooltip="New folder (in root)"
                onClick={() => setPendingCreate({ kind: "folder", parent: "" })}
              >
                <FolderPlus size={15} />
              </button>
              <button
                className="p-1 rounded hover:bg-surface text-muted hover:text-text"
                aria-label="New file"
                data-tooltip="New file (in root)"
                onClick={() => setPendingCreate({ kind: "file", parent: "" })}
              >
                <FilePlus2 size={15} />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-auto py-1">
            <FileTree
              files={files}
              activePath={activePath}
              statusByPath={statusMap(status)}
              onOpen={openFile}
              pendingCreate={pendingCreate}
              onStartCreate={(kind, parent) => setPendingCreate({ kind, parent })}
              onSubmitCreate={submitCreate}
              onCancelCreate={() => setPendingCreate(null)}
              onRename={handleRename}
              onMove={handleMove}
            />
          </div>
        </aside>

        <section className="flex-1 min-w-0 flex flex-col">
          {activePath ? (
            <>
              <div className="flex items-center gap-2 px-4 h-10 border-b border-border">
                <FileText size={15} className="text-muted shrink-0" />
                <span className="text-sm truncate flex-1" title={activePath}>
                  {activePath}
                  {dirty && <span className="text-accent"> •</span>}
                </span>
                {(isMarkdown(activePath) || isHtml(activePath)) && (
                  <div className="flex rounded border border-border overflow-hidden text-xs">
                    <button
                      className={`px-2 py-1 inline-flex items-center gap-1 ${
                        mode === "edit" ? "bg-surface" : "hover:bg-surface/60"
                      }`}
                      onClick={() => setMode("edit")}
                    >
                      <Pencil size={13} /> Edit
                    </button>
                    <button
                      className={`px-2 py-1 inline-flex items-center gap-1 ${
                        mode === "preview" ? "bg-surface" : "hover:bg-surface/60"
                      }`}
                      onClick={() => setMode("preview")}
                    >
                      <Eye size={13} /> Preview
                    </button>
                  </div>
                )}
                <button
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
                  disabled={!dirty || saving}
                  onClick={save}
                >
                  {saving ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Save size={13} />
                  )}
                  Save
                </button>
                <button
                  className={`p-1 rounded hover:bg-surface ${
                    copiedPath ? "text-green-500" : "text-muted hover:text-text"
                  }`}
                  aria-label="Copy local path"
                  data-tooltip={copiedPath ? "Copied!" : "Copy local path"}
                  onClick={() => copyFilePath(activePath)}
                >
                  {copiedPath ? (
                    <ClipboardCheck size={15} />
                  ) : (
                    <ClipboardCopy size={15} />
                  )}
                </button>
                <a
                  className="p-1 rounded text-muted hover:text-text hover:bg-surface"
                  href={workspaceRawUrl(area.slug, activePath)}
                  target="_blank"
                  rel="noreferrer"
                  aria-label="Open raw file"
                  data-tooltip="Open raw file in new tab"
                >
                  <ExternalLink size={15} />
                </a>
                <button
                  className="p-1 rounded text-muted hover:text-red-500 hover:bg-surface"
                  aria-label="Delete file"
                  data-tooltip="Delete file"
                  onClick={() => deleteFile(activePath)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
              <div className="flex-1 min-h-0 overflow-auto">
                {loadingFile ? (
                  <div className="h-full flex items-center justify-center text-muted">
                    <Loader2 className="animate-spin" size={18} />
                  </div>
                ) : !isEditable(activePath) ? (
                  <div className="p-6 text-muted text-sm">
                    This file type isn't editable here. Use the git CLI to manage it.
                  </div>
                ) : mode === "preview" && isMarkdown(activePath) ? (
                  <Markdown className="text-sm leading-relaxed p-6 max-w-3xl">
                    {content || "\u200B"}
                  </Markdown>
                ) : mode === "preview" && isHtml(activePath) ? (
                  <iframe
                    title={activePath}
                    src={workspaceRawUrl(area.slug, activePath)}
                    sandbox="allow-scripts allow-same-origin"
                    className="w-full h-full border-0 bg-white"
                  />
                ) : (
                  <textarea
                    className="w-full h-full resize-none bg-bg text-text font-mono text-sm p-4 outline-none"
                    value={content}
                    spellCheck={false}
                    onChange={(e) => setContent(e.target.value)}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="h-full flex items-center justify-center text-muted text-sm">
              Select a file to view or edit.
            </div>
          )}
        </section>

        <WorkspaceChat
          area={area}
          activePath={activePath}
          onSetRole={onSetRole}
          onOpenRoleSelector={onOpenRoleSelector}
        />
      </div>
    </div>
  );
}

function statusMap(status: GitStatus | null): Map<string, string> {
  const m = new Map<string, string>();
  for (const f of status?.files ?? []) m.set(f.path, f.code);
  return m;
}

// --------------------------------------------------------------------------
// Local workspace header bar (no git — just folder path + delete)
// --------------------------------------------------------------------------

function LocalWorkspaceBar({
  area,
  onDeleted,
  onError,
}: {
  area: Workspace;
  onDeleted: () => void;
  onError: (msg: string | null) => void;
}) {
  const confirmAction = useConfirm();
  const [copied, setCopied] = useState(false);

  async function copyLocalPath(): Promise<void> {
    onError(null);
    try {
      const { path } = await api.workspaces.localPath(area.id);
      await navigator.clipboard.writeText(path);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeArea(): Promise<void> {
    if (
      !(await confirmAction({
        message: `Remove "${area.name}"? This deletes the local folder and its files.`,
        confirmLabel: "Remove workspace",
        variant: "danger",
      }))
    )
      return;
    try {
      await api.workspaces.remove(area.id);
      onDeleted();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex items-center gap-3 px-4 h-10 border-b border-border bg-surface/40 text-sm">
      <span className="inline-flex items-center gap-1.5 text-muted">
        <FolderOpen size={14} />
        Local folder
      </span>
      <button
        className="p-1 rounded hover:bg-surface text-muted hover:text-text"
        aria-label="Copy local path"
        data-tooltip={copied ? "Copied!" : "Copy local folder path"}
        onClick={() => void copyLocalPath()}
      >
        {copied ? (
          <ClipboardCheck size={13} className="text-emerald-500" />
        ) : (
          <ClipboardCopy size={13} />
        )}
      </button>
      <div className="flex-1" />
      <button
        className="p-1.5 rounded hover:bg-surface text-muted hover:text-red-500"
        aria-label="Remove workspace"
        data-tooltip="Remove workspace (deletes the local folder)"
        onClick={removeArea}
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

// --------------------------------------------------------------------------
// Git header bar
// --------------------------------------------------------------------------

function GitBar({
  area,
  status,
  dirty,
  onRefreshStatus,
  onAfterSync,
  onDeleted,
  onError,
}: {
  area: Workspace;
  status: GitStatus | null;
  dirty: boolean;
  onRefreshStatus: () => Promise<void>;
  onAfterSync: () => Promise<void>;
  onDeleted: () => void;
  onError: (msg: string | null) => void;
}) {
  const confirmAction = useConfirm();
  const [busy, setBusy] = useState<"pull" | "push" | null>(null);
  const [conflict, setConflict] = useState<{ detail: string; path: string } | null>(
    null,
  );
  const [reviewing, setReviewing] = useState(false);
  const [copied, setCopied] = useState(false);

  const changeCount = status?.files.length ?? 0;

  async function copyLocalPath(): Promise<void> {
    onError(null);
    try {
      const { path } = await api.workspaces.localPath(area.id);
      await navigator.clipboard.writeText(path);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }

  async function pull(): Promise<void> {
    setBusy("pull");
    onError(null);
    setConflict(null);
    try {
      const res = await api.workspaces.gitPull(area.id);
      if (!res.ok && res.needs_manual_merge) {
        setConflict({ detail: res.detail, path: res.local_path ?? "" });
      }
      await onAfterSync();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function commitPushPaths(
    message: string,
    paths: string[],
  ): Promise<GitActionResult> {
    onError(null);
    setConflict(null);
    const res = await api.workspaces.gitCommitPush(area.id, message, paths);
    if (!res.ok) {
      if (res.needs_manual_merge) {
        setConflict({ detail: res.detail, path: res.local_path ?? "" });
      } else {
        onError(res.detail);
      }
    }
    await onAfterSync();
    return res;
  }

  async function discardPath(path: string): Promise<void> {
    onError(null);
    try {
      await api.workspaces.gitDiscard(area.id, path);
      await onAfterSync();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeArea(): Promise<void> {
    if (
      !(await confirmAction({
        message:
          `Remove "${area.name}"? This deletes the local working copy (the remote repo is untouched).`,
        confirmLabel: "Remove workspace",
        variant: "danger",
      }))
    )
      return;
    try {
      await api.workspaces.remove(area.id);
      onDeleted();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <div className="flex items-center gap-3 px-4 h-10 border-b border-border bg-surface/40 text-sm">
        <span className="inline-flex items-center gap-1.5 text-muted">
          <GitBranch size={14} />
          {status?.branch ?? area.branch}
        </span>
        {status && (status.behind ?? 0) > 0 && (
          <span className="text-amber-500">↓ {status.behind} behind</span>
        )}
        {status && (status.ahead ?? 0) > 0 && (
          <span className="text-blue-500">↑ {status.ahead} ahead</span>
        )}
        <span className="text-muted">
          {changeCount > 0
            ? `${changeCount} uncommitted change${changeCount === 1 ? "" : "s"}`
            : "clean"}
        </span>
        <button
          className="p-1 rounded hover:bg-surface text-muted hover:text-text"
          aria-label="Refresh status"
          data-tooltip="Refresh status"
          onClick={() => void onRefreshStatus()}
        >
          <RefreshCw size={13} />
        </button>
        <button
          className="p-1 rounded hover:bg-surface text-muted hover:text-text"
          aria-label="Copy local path"
          data-tooltip={copied ? "Copied!" : "Copy local folder path"}
          onClick={() => void copyLocalPath()}
        >
          {copied ? (
            <ClipboardCheck size={13} className="text-emerald-500" />
          ) : (
            <ClipboardCopy size={13} />
          )}
        </button>

        <div className="flex-1" />

        <button
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-border hover:bg-surface text-xs disabled:opacity-50"
          disabled={busy !== null}
          onClick={pull}
        >
          {busy === "pull" ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Download size={13} />
          )}
          Pull
        </button>
        <button
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
          disabled={busy !== null || (changeCount === 0 && !dirty)}
          onClick={() => setReviewing(true)}
        >
          <Upload size={13} />
          Review &amp; Push
          {changeCount > 0 && (
            <span className="ml-0.5 px-1 rounded bg-white/20 text-[10px] leading-4">
              {changeCount}
            </span>
          )}
        </button>
        <button
          className="p-1.5 rounded hover:bg-surface text-muted hover:text-red-500"
          aria-label="Remove workspace"
          data-tooltip="Remove workspace (keeps remote repo)"
          onClick={removeArea}
        >
          <Trash2 size={14} />
        </button>
      </div>

      {conflict && (
        <div className="px-4 py-3 text-sm bg-amber-500/10 border-b border-border space-y-1">
          <p className="font-medium text-amber-600 dark:text-amber-400">
            Couldn&apos;t sync automatically — manual merge needed.
          </p>
          <p className="text-muted">{conflict.detail}</p>
          {conflict.path && (
            <p className="text-muted">
              Resolve it from a terminal, then click Pull again:
              <code className="ml-1 px-1.5 py-0.5 rounded bg-surface font-mono text-xs">
                cd {conflict.path} &amp;&amp; git status
              </code>
            </p>
          )}
          <button
            className="text-xs underline text-muted hover:text-text"
            onClick={() => setConflict(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {reviewing && (
        <ChangesModal
          area={area}
          files={status?.files ?? []}
          onClose={() => setReviewing(false)}
          onCommitPush={commitPushPaths}
          onDiscard={discardPath}
        />
      )}
    </>
  );
}

// --------------------------------------------------------------------------
// Create-workspace modal
// --------------------------------------------------------------------------

export function CreateWorkspaceModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (workspace: Workspace) => void;
}) {
  const [kind, setKind] = useState<"git" | "local">("git");
  const [name, setName] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [subdir, setSubdir] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isGit = kind === "git";
  const canSubmit = name.trim().length > 0 && (!isGit || repoUrl.trim().length > 0);

  async function submit(): Promise<void> {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const workspace = await api.workspaces.create(
        isGit
          ? {
              name: name.trim(),
              kind: "git",
              repo_url: repoUrl.trim(),
              branch: branch.trim() || "main",
              subdir: subdir.trim() || null,
            }
          : { name: name.trim(), kind: "local" },
      );
      onCreated(workspace);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[28rem] max-w-[92vw] rounded-lg border border-border bg-bg shadow-xl p-5 space-y-3">
        <h2 className="font-medium">New workspace</h2>
        <div className="flex gap-1 rounded border border-border p-0.5 bg-surface text-sm">
          <button
            type="button"
            onClick={() => setKind("git")}
            aria-pressed={isGit}
            className={`flex-1 rounded px-2 py-1 ${
              isGit ? "bg-accent text-white" : "text-muted hover:text-text"
            }`}
          >
            Git repository
          </button>
          <button
            type="button"
            onClick={() => setKind("local")}
            aria-pressed={!isGit}
            className={`flex-1 rounded px-2 py-1 ${
              !isGit ? "bg-accent text-white" : "text-muted hover:text-text"
            }`}
          >
            Local folder
          </button>
        </div>
        <p className="text-xs text-muted">
          {isGit ? (
            <>
              Clones the repository into a local working copy. Uses your configured
              GitHub token for private repos. Requires <code>git</code> on the server.
            </>
          ) : (
            <>
              Creates an empty folder for file authoring. No git — just view and edit
              files. You can use the workspace filesystem tools on it too.
            </>
          )}
        </p>
        <label className="block text-sm">
          <span className="text-muted">Name</span>
          <input
            className="mt-1 w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={isGit ? "Team handbook" : "Scratch notes"}
            autoFocus
          />
        </label>
        {isGit && (
          <>
            <label className="block text-sm">
              <span className="text-muted">Repository URL (HTTPS)</span>
              <input
                className="mt-1 w-full bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono outline-none focus:border-accent"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/owner/repo.git"
              />
            </label>
            <div className="flex gap-3">
              <label className="block text-sm flex-1">
                <span className="text-muted">Branch</span>
                <input
                  className="mt-1 w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                />
              </label>
              <label className="block text-sm flex-1">
                <span className="text-muted">Subdirectory (optional)</span>
                <input
                  className="mt-1 w-full bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono outline-none focus:border-accent"
                  value={subdir}
                  onChange={(e) => setSubdir(e.target.value)}
                  placeholder="docs/"
                />
              </label>
            </div>
          </>
        )}
        {error && <p className="text-sm text-red-500">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
            onClick={submit}
            disabled={busy || !canSubmit}
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            {isGit ? "Clone" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
