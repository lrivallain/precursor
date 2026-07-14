import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Check,
  ClipboardCheck,
  ClipboardCopy,
  Code2,
  Copy,
  Download,
  ExternalLink,
  Eye,
  FilePlus2,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  GitBranch,
  Loader2,
  MessageSquare,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  Square,
  SquareCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, workspaceRawUrl } from "../lib/api";
import { parseSlashCommand, SLASH_COMMANDS } from "../lib/commands";
import type { SlashCommand } from "../lib/commands";
import { mcpAuthStore } from "../lib/mcpAuth";
import { skillsStore, useSkills } from "../lib/skillsStore";
import { rolesStore } from "../lib/rolesStore";
import { useSettings } from "../lib/settingsStore";
import { streamWorkspaceChat } from "../lib/sse";
import { stripSuggestionBlock } from "../lib/suggestions";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useConfirm } from "./ConfirmDialog";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { InlineTitle } from "./InlineTitle";
import { Markdown } from "./Markdown";
import { ResizeHandle } from "./ResizeHandle";
import { SuggestedReplies } from "./SuggestedReplies";
import { ToolCallBubble } from "./ToolCallBubble";
import type {
  FileDiff,
  GitActionResult,
  GitFileStatus,
  GitStatus,
  Workspace,
  WorkspaceChatMessage,
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
// Changes review modal — preview diffs, choose which files to commit
// --------------------------------------------------------------------------

function ChangesModal({
  area,
  files,
  onClose,
  onCommitPush,
  onDiscard,
}: {
  area: Workspace;
  files: GitFileStatus[];
  onClose: () => void;
  onCommitPush: (message: string, paths: string[]) => Promise<GitActionResult>;
  onDiscard: (path: string) => Promise<void>;
}) {
  const confirmAction = useConfirm();
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(files.map((f) => f.path)),
  );
  const [active, setActive] = useState<string | null>(files[0]?.path ?? null);
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [message, setMessage] = useState("Update workspace content");
  const [pushing, setPushing] = useState(false);

  const loadDiff = useCallback(
    async (path: string): Promise<void> => {
      setActive(path);
      setLoadingDiff(true);
      try {
        setDiff(await api.workspaces.gitDiff(area.id, path));
      } catch {
        setDiff({ path, diff: "(failed to load diff)", binary: false });
      } finally {
        setLoadingDiff(false);
      }
    },
    [area.id],
  );

  useEffect(() => {
    if (active) void loadDiff(active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggle(path: string): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const allSelected = files.length > 0 && selected.size === files.length;
  function toggleAll(): void {
    setSelected(allSelected ? new Set() : new Set(files.map((f) => f.path)));
  }

  async function commit(): Promise<void> {
    if (selected.size === 0 || !message.trim()) return;
    setPushing(true);
    try {
      const res = await onCommitPush(message.trim(), [...selected]);
      if (res.ok) onClose();
    } finally {
      setPushing(false);
    }
  }

  async function discard(path: string): Promise<void> {
    if (
      !(await confirmAction({
        message: `Discard local changes to "${path}"?`,
        confirmLabel: "Discard changes",
        variant: "warning",
      }))
    )
      return;
    await onDiscard(path);
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(path);
      return next;
    });
    if (active === path) {
      setActive(null);
      setDiff(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-bg border border-border rounded-lg shadow-xl w-full max-w-4xl h-[80vh] flex flex-col">
        <div className="flex items-center gap-2 px-4 h-12 border-b border-border">
          <Upload size={16} className="text-muted" />
          <span className="font-medium">Review changes</span>
          <span className="text-xs text-muted">
            {files.length} file{files.length === 1 ? "" : "s"} changed ·{" "}
            {selected.size} selected
          </span>
          <div className="flex-1" />
          <button
            className="p-1.5 rounded hover:bg-surface text-muted hover:text-text"
            aria-label="Close"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 min-h-0 flex">
          <div className="w-64 shrink-0 border-r border-border flex flex-col min-h-0">
            <button
              className="flex items-center gap-2 px-3 h-9 border-b border-border text-xs text-muted hover:bg-surface/60"
              onClick={toggleAll}
            >
              {allSelected ? (
                <SquareCheck size={14} />
              ) : (
                <Square size={14} />
              )}
              {allSelected ? "Unselect all" : "Select all"}
            </button>
            <div className="flex-1 overflow-auto py-1">
              {files.length === 0 && (
                <p className="px-3 py-2 text-xs text-muted">No changes.</p>
              )}
              {files.map((f) => {
                const isActive = active === f.path;
                const isSel = selected.has(f.path);
                return (
                  <div
                    key={f.path}
                    className={`flex items-center gap-1 pr-1 pl-2 ${
                      isActive ? "bg-surface" : "hover:bg-surface/60"
                    }`}
                  >
                    <button
                      className="shrink-0 p-1 text-muted hover:text-text"
                      aria-label={isSel ? "Unstage" : "Stage"}
                      onClick={() => toggle(f.path)}
                    >
                      {isSel ? (
                        <SquareCheck size={15} className="text-accent" />
                      ) : (
                        <Square size={15} />
                      )}
                    </button>
                    <button
                      className="flex-1 flex items-center gap-1.5 py-1.5 text-sm text-left min-w-0"
                      onClick={() => void loadDiff(f.path)}
                    >
                      <span
                        className="shrink-0 w-4 text-center text-[10px] font-mono text-amber-500"
                        title={`git: ${f.code.trim() || f.code}`}
                      >
                        {f.code.trim() === "??" ? "U" : f.code.trim() || "M"}
                      </span>
                      <span className="truncate" title={f.path}>
                        {f.path}
                      </span>
                    </button>
                    <button
                      className="shrink-0 p-1 text-muted hover:text-red-500"
                      aria-label={`Discard ${f.path}`}
                      data-tooltip="Discard changes"
                      onClick={() => void discard(f.path)}
                    >
                      <RotateCcw size={13} />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex-1 min-w-0 overflow-auto bg-surface/30">
            {loadingDiff ? (
              <div className="h-full flex items-center justify-center text-muted">
                <Loader2 className="animate-spin" size={18} />
              </div>
            ) : !active ? (
              <div className="h-full flex items-center justify-center text-muted text-sm">
                Select a file to preview its changes.
              </div>
            ) : diff?.binary ? (
              <div className="p-6 text-muted text-sm">
                Binary file — diff not shown.
              </div>
            ) : (
              <DiffView text={diff?.diff ?? ""} />
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 px-4 h-14 border-t border-border">
          <input
            className="flex-1 px-3 py-1.5 rounded border border-border bg-bg text-sm outline-none focus:border-accent"
            placeholder="Commit message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <button
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
            disabled={pushing || selected.size === 0 || !message.trim()}
            onClick={commit}
          >
            {pushing ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Upload size={14} />
            )}
            Commit &amp; Push ({selected.size})
          </button>
        </div>
      </div>
    </div>
  );
}

function DiffView({ text }: { text: string }): ReactNode {
  if (!text.trim()) {
    return (
      <div className="h-full flex items-center justify-center text-muted text-sm">
        No textual changes.
      </div>
    );
  }
  return (
    <pre className="text-xs font-mono leading-relaxed p-3 whitespace-pre">
      {text.split("\n").map((line, i) => {
        let cls = "text-text";
        if (line.startsWith("+") && !line.startsWith("+++")) {
          cls = "text-green-600 dark:text-green-400 bg-green-500/10";
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          cls = "text-red-600 dark:text-red-400 bg-red-500/10";
        } else if (line.startsWith("@@")) {
          cls = "text-accent";
        } else if (
          line.startsWith("diff ") ||
          line.startsWith("index ") ||
          line.startsWith("+++") ||
          line.startsWith("---")
        ) {
          cls = "text-muted";
        }
        return (
          <div key={i} className={`${cls} px-1`}>
            {line || "\u200B"}
          </div>
        );
      })}
    </pre>
  );
}

// --------------------------------------------------------------------------
// File tree (collapsible)
// --------------------------------------------------------------------------

interface TreeNode {
  node: WorkspaceFileNode;
  children: TreeNode[];
}

function buildTree(files: WorkspaceFileNode[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const byPath = new Map<string, TreeNode>();
  for (const node of files) {
    const tn: TreeNode = { node, children: [] };
    byPath.set(node.path, tn);
    const slash = node.path.lastIndexOf("/");
    if (slash === -1) {
      roots.push(tn);
    } else {
      const parent = byPath.get(node.path.slice(0, slash));
      if (parent) parent.children.push(tn);
      else roots.push(tn);
    }
  }
  return roots;
}

// One inline "type the name here" row rendered in the tree at the create
// target (VS Code style). Enter confirms, Escape or blur abandons.
function CreateRow({
  kind,
  depth,
  onSubmit,
  onCancel,
}: {
  kind: "file" | "folder";
  depth: number;
  onSubmit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const indent = { paddingLeft: `${depth * 12 + 8}px` };
  return (
    <div className="flex items-center gap-1.5 pr-2 py-0.5" style={indent}>
      {kind === "file" ? (
        <FileText size={14} className="shrink-0 text-muted" />
      ) : (
        <Folder size={14} className="shrink-0 text-muted" />
      )}
      <input
        autoFocus
        spellCheck={false}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onSubmit(value);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
        onBlur={onCancel}
        placeholder={kind === "file" ? "filename.md" : "folder-name"}
        className="flex-1 min-w-0 bg-bg border border-accent rounded px-1.5 py-0.5 text-sm font-mono outline-none"
      />
    </div>
  );
}

function FileTree({
  files,
  activePath,
  statusByPath,
  onOpen,
  pendingCreate,
  onStartCreate,
  onSubmitCreate,
  onCancelCreate,
  onRename,
  onMove,
}: {
  files: WorkspaceFileNode[];
  activePath: string | null;
  statusByPath: Map<string, string>;
  onOpen: (path: string) => void;
  pendingCreate: { kind: "file" | "folder"; parent: string } | null;
  onStartCreate: (kind: "file" | "folder", parent: string) => void;
  onSubmitCreate: (name: string) => void;
  onCancelCreate: () => void;
  onRename: (path: string, newName: string) => Promise<void>;
  onMove: (src: string, targetDir: string) => Promise<void>;
}) {
  const tree = useMemo(() => buildTree(files), [files]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // Drag-and-drop move state: the path being dragged and the folder ("" = root)
  // currently hovered as a drop target.
  const [dragSrc, setDragSrc] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  // A drop is valid unless it targets the item itself, one of its descendants,
  // or the folder it already lives in (a no-op).
  const canDrop = (src: string | null, targetDir: string): boolean => {
    if (!src) return false;
    if (targetDir === src || targetDir.startsWith(`${src}/`)) return false;
    const slash = src.lastIndexOf("/");
    const parent = slash === -1 ? "" : src.slice(0, slash);
    return parent !== targetDir;
  };

  const handleDrop = (targetDir: string): void => {
    const src = dragSrc;
    setDragSrc(null);
    setDropTarget(null);
    if (canDrop(src, targetDir) && src) void onMove(src, targetDir).catch(() => {});
  };

  // Drag handlers shared by file and folder rows. Renaming inputs are excluded
  // so text selection inside InlineTitle isn't hijacked by row dragging.
  const dragProps = (path: string) => ({
    draggable: true,
    onDragStart: (e: React.DragEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") {
        e.preventDefault();
        return;
      }
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", path);
      setDragSrc(path);
    },
    onDragEnd: () => {
      setDragSrc(null);
      setDropTarget(null);
    },
  });

  // Drop handlers for a folder ("" = the root container).
  const dropProps = (targetDir: string) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!canDrop(dragSrc, targetDir)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "move";
      setDropTarget(targetDir);
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      handleDrop(targetDir);
    },
  });

  // Collapse all folders once when the area's files first load. FileTree is
  // keyed by area id (it remounts on area switch), so this runs per area.
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current || files.length === 0) return;
    initialized.current = true;
    setCollapsed(
      new Set(files.filter((f) => f.type === "dir").map((f) => f.path)),
    );
  }, [files]);

  // Expand a folder the moment it becomes the create target so its inline
  // input row is visible.
  useEffect(() => {
    const parent = pendingCreate?.parent;
    if (!parent) return;
    setCollapsed((prev) => {
      if (!prev.has(parent)) return prev;
      const next = new Set(prev);
      next.delete(parent);
      return next;
    });
  }, [pendingCreate]);

  const renderCreateRow = (parent: string, depth: number): ReactNode =>
    pendingCreate && pendingCreate.parent === parent ? (
      <CreateRow
        kind={pendingCreate.kind}
        depth={depth}
        onSubmit={onSubmitCreate}
        onCancel={onCancelCreate}
      />
    ) : null;

  if (files.length === 0) {
    return (
      <div>
        {renderCreateRow("", 0) ?? <p className="px-3 py-2 text-xs text-muted">No files.</p>}
      </div>
    );
  }

  function toggle(path: string): void {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const renderNodes = (nodes: TreeNode[], depth: number): ReactNode =>
    nodes.map((tn) => {
      const { node } = tn;
      const indent = { paddingLeft: `${depth * 12 + 8}px` };
      if (node.type === "dir") {
        const isCollapsed = collapsed.has(node.path);
        const isDropHere = dropTarget === node.path;
        return (
          <div key={node.path}>
            <div
              className={`group flex items-center gap-1 pr-1 text-sm text-muted cursor-pointer ${
                isDropHere ? "bg-accent/15 ring-1 ring-inset ring-accent/50" : "hover:bg-surface"
              }`}
              style={indent}
              onClick={() => toggle(node.path)}
              {...dragProps(node.path)}
              {...dropProps(node.path)}
            >
              {isCollapsed ? (
                <ChevronRight size={13} className="shrink-0" />
              ) : (
                <ChevronDown size={13} className="shrink-0" />
              )}
              {isCollapsed ? (
                <Folder size={14} className="shrink-0" />
              ) : (
                <FolderOpen size={14} className="shrink-0" />
              )}
              <InlineTitle
                title={node.name}
                onRename={(name) => onRename(node.path, name)}
                className="flex-1 truncate py-1"
              />
              <div className="flex items-center gap-0.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                <button
                  className="p-0.5 rounded hover:bg-bg text-muted hover:text-text"
                  aria-label={`New file in ${node.name}`}
                  data-tooltip="New file here"
                  onClick={(e) => {
                    e.stopPropagation();
                    onStartCreate("file", node.path);
                  }}
                >
                  <FilePlus2 size={13} />
                </button>
                <button
                  className="p-0.5 rounded hover:bg-bg text-muted hover:text-text"
                  aria-label={`New folder in ${node.name}`}
                  data-tooltip="New folder here"
                  onClick={(e) => {
                    e.stopPropagation();
                    onStartCreate("folder", node.path);
                  }}
                >
                  <FolderPlus size={13} />
                </button>
              </div>
            </div>
            {!isCollapsed && (
              <>
                {renderCreateRow(node.path, depth + 1)}
                {renderNodes(tn.children, depth + 1)}
              </>
            )}
          </div>
        );
      }
      const code = statusByPath.get(node.path);
      const active = activePath === node.path;
      return (
        <div
          key={node.path}
          className={`group flex items-center gap-1.5 pr-2 py-1 text-sm cursor-pointer ${
            active ? "bg-surface" : "hover:bg-surface/60"
          }`}
          style={indent}
          onClick={() => onOpen(node.path)}
          {...dragProps(node.path)}
        >
          <FileText size={14} className="shrink-0 text-muted" />
          <InlineTitle
            title={node.name}
            onRename={(name) => onRename(node.path, name)}
            className="flex-1 truncate"
          />
          {code && (
            <span
              className="text-[10px] font-mono text-amber-500 shrink-0"
              title={`git: ${code.trim() || code}`}
            >
              {code.trim() === "??" ? "U" : code.trim() || "M"}
            </span>
          )}
        </div>
      );
    });

  // The whole tree is a root drop zone: dropping outside any folder moves the
  // item to the workspace root. A subtle ring shows when root is the target.
  const rootActive = dragSrc !== null && dropTarget === "" && canDrop(dragSrc, "");
  return (
    <div
      className={`min-h-full ${rootActive ? "ring-1 ring-inset ring-accent/40" : ""}`}
      {...dropProps("")}
    >
      {renderCreateRow("", 0)}
      {renderNodes(tree, 0)}
    </div>
  );
}

// --------------------------------------------------------------------------
// Chat panel bound to the active file
// --------------------------------------------------------------------------

// A rendered chat entry: a text turn or an MCP tool call/result. Tool entries
// are display-only; only user/assistant text is sent back as history.
type WorkspaceChatItem =
  | { kind: "user"; content: string }
  | { kind: "assistant"; content: string; suggestions?: string[] }
  | {
      kind: "tool";
      name: string;
      arguments: string;
      content: string | null;
      isError: boolean;
      pending: boolean;
    };

const CHAT_COLLAPSE_KEY = "precursor:workspace:chat-collapsed";

function WorkspaceChat({
  area,
  activePath,
  onSetRole,
  onOpenRoleSelector,
}: {
  area: Workspace;
  activePath: string | null;
  onSetRole?: (roleId: number | null) => Promise<void>;
  onOpenRoleSelector?: () => void;
}) {
  const [messages, setMessages] = useState<WorkspaceChatItem[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Collapse the assistant into a thin rail (persisted), mirroring the
  // conversation-stats aside on topics/chats. Kept mounted so chat state and
  // any in-flight stream survive a collapse.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(CHAT_COLLAPSE_KEY) === "1";
  });
  useEffect(() => {
    window.localStorage.setItem(CHAT_COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Resizable assistant panel (right) + message composer (bottom).
  const { width: panelWidth, onMouseDown: onPanelResize } = useResizableWidth({
    storageKey: "precursor:workspace:chatWidth",
    defaultWidth: 384,
    min: 280,
    max: 720,
    side: "left",
  });
  const { height: composerHeight, onMouseDown: onComposerResize } =
    useResizableHeight({
      storageKey: "precursor:workspace:chatComposerHeight",
      defaultHeight: 56,
      min: 40,
      max: 320,
    });

  const settings = useSettings();

  // Live speech-to-text (when Azure is configured server-side).
  const [interimText, setInterimText] = useState("");
  const speech = useAzureSpeech({
    onFinalChunk: (text) => {
      const chunk = text.trim();
      if (!chunk) return;
      setInput((d) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk));
      setInterimText("");
    },
    onInterim: setInterimText,
    enabled: settings?.stt_azure_ready ?? false,
    lang: settings?.azure_speech_language || undefined,
  });
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

  // Autocomplete: only the commands this surface actually handles in `send`
  // (skills + `/role`), so the picker never offers commands the backend rejects.
  const skills = useSkills();
  const wsCommands = useMemo<SlashCommand[]>(() => {
    const role = SLASH_COMMANDS.find((c) => c.name === "role");
    const skillCommands: SlashCommand[] = skills
      .filter((s) => s.active)
      .map((s) => ({
        name: s.name,
        label: `/${s.name}`,
        description: s.description ?? "",
        kind: "skill" as const,
        argumentHint: "input",
      }));
    return [...(role ? [role] : []), ...skillCommands];
  }, [skills]);
  const suggestions = useMemo<SlashCommand[]>(() => {
    if (!input.startsWith("/") || /\s/.test(input)) return [];
    const q = input.slice(1).toLowerCase();
    return wsCommands.filter((c) => c.name.startsWith(q));
  }, [input, wsCommands]);
  const userHistory = useMemo(
    () => messages.filter((m) => m.kind === "user").map((m) => m.content),
    [messages],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pending]);

  async function send(explicit?: string): Promise<void> {
    const content = (explicit ?? input).trim();
    if (!content || streaming) return;
    if (explicit === undefined) setInput("");
    setError(null);

    // Skills: a `/skill-name argument` invocation is expanded into the prompt
    // the model receives, while the UI keeps showing the literal command.
    let promptOverride: string | undefined;
    const cmd = parseSlashCommand(
      content,
      skillsStore
        .all()
        .filter((s) => s.active)
        .map((s) => ({
          name: s.name,
          label: `/${s.name}`,
          description: s.description ?? "",
          kind: "skill" as const,
        })),
    );
    if (cmd && cmd.name === "role") {
      const arg = cmd.argument.trim();
      if (!arg) {
        onOpenRoleSelector?.();
        return;
      }
      await rolesStore.ensureLoaded();
      const role = rolesStore.byName(arg);
      if (!role) {
        setError(`Unknown role "${arg}". Manage roles in Settings → Roles.`);
        return;
      }
      try {
        await onSetRole?.(role.is_default ? null : role.id);
      } catch (err) {
        setError(`Role change failed: ${(err as Error).message}`);
      }
      return;
    }
    if (cmd) {
      const skill = skillsStore.byName(cmd.name);
      if (skill) {
        promptOverride = `${skill.instructions.trim()}\n\n---\n\n${cmd.argument}`;
      }
    }

    // History sent to the backend is only the user/assistant text turns.
    const history: WorkspaceChatMessage[] = messages
      .filter(
        (m): m is Extract<WorkspaceChatItem, { kind: "user" | "assistant" }> =>
          m.kind === "user" || m.kind === "assistant",
      )
      .map((m) => ({ role: m.kind, content: m.content }));
    setMessages((m) => [...m, { kind: "user", content }]);
    setStreaming(true);
    setPending("");
    const controller = new AbortController();
    abortRef.current = controller;
    let acc = "";
    let turnSuggestions: string[] = [];
    // Tool items emitted during this turn, keyed by tool_call_id so results can
    // be matched back to the call that produced them.
    const toolItems = new Map<string, WorkspaceChatItem & { kind: "tool" }>();
    try {
      await streamWorkspaceChat(
        area.id,
        {
          content,
          history,
          path: activePath,
          ...(promptOverride ? { prompt_override: promptOverride } : {}),
        },
        {
          signal: controller.signal,
          onEvent: (e) => {
            if (e.event === "delta") {
              const { content: c } = JSON.parse(e.data) as { content: string };
              acc += c;
              setPending(acc);
            } else if (e.event === "tool_calls") {
              // Fold any streamed text so far into an assistant bubble, then
              // append a pending tool bubble per call.
              const { calls } = JSON.parse(e.data) as {
                calls: { id: string; name: string; arguments: string }[];
              };
              setMessages((prev) => {
                const next = [...prev];
                if (acc.trim())
                  next.push({ kind: "assistant", content: stripSuggestionBlock(acc) });
                for (const call of calls) {
                  const item: WorkspaceChatItem & { kind: "tool" } = {
                    kind: "tool",
                    name: call.name,
                    arguments: call.arguments,
                    content: null,
                    isError: false,
                    pending: true,
                  };
                  toolItems.set(call.id, item);
                  next.push(item);
                }
                return next;
              });
              acc = "";
              setPending("");
            } else if (e.event === "tool_result") {
              const r = JSON.parse(e.data) as {
                tool_call_id: string;
                content: string;
                is_error: boolean;
              };
              setMessages((prev) =>
                prev.map((m) =>
                  m.kind === "tool" && m === toolItems.get(r.tool_call_id)
                    ? { ...m, content: r.content, isError: r.is_error, pending: false }
                    : m,
                ),
              );
            } else if (e.event === "suggestions") {
              const { items } = JSON.parse(e.data) as { items?: string[] };
              turnSuggestions = items ?? [];
            } else if (e.event === "mcp_auth_required") {
              const { server, message } = JSON.parse(e.data) as {
                server: string;
                message: string;
              };
              mcpAuthStore.report(server ?? "workiq", message ?? "Sign-in required.");
            } else if (e.event === "system") {
              const { message } = JSON.parse(e.data) as { message: string };
              setError(message);
            } else if (e.event === "error") {
              const { message } = JSON.parse(e.data) as { message: string };
              setError(message);
            }
          },
        },
      );
      if (acc.trim()) {
        setMessages((m) => [
          ...m,
          {
            kind: "assistant",
            content: stripSuggestionBlock(acc),
            suggestions: turnSuggestions,
          },
        ]);
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setStreaming(false);
      setPending("");
      abortRef.current = null;
    }
  }

  if (collapsed) {
    return (
      <aside className="relative shrink-0 border-l border-border flex flex-col items-center py-2 px-1 w-9">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="p-1.5 rounded hover:bg-surface text-muted"
          data-tooltip="Show assistant"
          aria-label="Show assistant"
        >
          <ChevronLeft size={16} />
        </button>
        <MessageSquare size={16} className="mt-2 text-muted" />
        {streaming && <Loader2 size={14} className="mt-2 animate-spin text-muted" />}
      </aside>
    );
  }

  return (
    <aside
      className="relative shrink-0 border-l border-border flex flex-col min-h-0"
      style={{ width: panelWidth }}
    >
      <ResizeHandle onMouseDown={onPanelResize} side="left" />
      <div className="flex items-center justify-between px-3 h-10 border-b border-border">
        <span className="text-xs font-medium text-muted uppercase tracking-wide">
          Assistant
        </span>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              className="text-xs text-muted hover:text-text"
              onClick={() => setMessages([])}
            >
              Clear
            </button>
          )}
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="p-1 rounded hover:bg-surface text-muted"
            data-tooltip="Hide assistant"
            aria-label="Hide assistant"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-auto p-3 space-y-3">
        {messages.length === 0 && !streaming && (
          <p className="text-sm text-muted">
            Ask for help drafting or improving
            {activePath ? (
              <>
                {" "}
                <code className="px-1 rounded bg-surface text-xs">{activePath}</code>.
              </>
            ) : (
              " your workspace content."
            )}
          </p>
        )}
        {messages.map((m, i) =>
          m.kind === "tool" ? (
            <ToolCallBubble
              key={i}
              name={m.name}
              arguments={m.arguments}
              content={m.content}
              isError={m.isError}
              pending={m.pending}
            />
          ) : (
            <ChatTurn key={i} role={m.kind} content={m.content} />
          ),
        )}
        {!streaming &&
          (() => {
            const last = messages[messages.length - 1];
            if (last?.kind === "assistant" && last.suggestions?.length) {
              return (
                <SuggestedReplies
                  items={last.suggestions}
                  onPick={(t) => void send(t)}
                  disabled={streaming}
                />
              );
            }
            return null;
          })()}
        {streaming && pending && (
          <ChatTurn role="assistant" content={stripSuggestionBlock(pending)} pending />
        )}
        {error && <p className="text-sm text-red-500">{error}</p>}
      </div>
      <div className="border-t border-border p-2">
        <Composer
          value={input}
          onChange={setInput}
          onSend={() => void send()}
          onStop={() => abortRef.current?.abort()}
          streaming={streaming}
          suggestions={suggestions}
          userHistory={userHistory}
          speech={speech}
          interimText={interimText}
          height={composerHeight}
          onResizeStart={onComposerResize}
          placeholder={activePath ? `Improve ${activePath}…` : "Ask the assistant…"}
          toolbarStart={<ComposerModelControls />}
        />
      </div>
    </aside>
  );
}

function ChatTurn({
  role,
  content,
  pending,
}: {
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const [copied, setCopied] = useState<null | "text" | "md">(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // Copy the rendered text (markdown stripped) or the raw markdown source —
  // mirrors the main chat's MessageBubble actions.
  const copyTo = async (kind: "text" | "md") => {
    const value =
      kind === "md" ? content : (contentRef.current?.textContent ?? content).trim();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  };

  const showActions = role === "assistant" && !pending && !!content;

  return (
    <div
      className={`group relative rounded-lg px-3 py-2 text-sm ${
        role === "user" ? "bg-accent/10 ml-6" : "bg-surface mr-6"
      }`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div ref={contentRef}>
        <Markdown className="leading-relaxed">{content || "\u200B"}</Markdown>
      </div>
      {pending && <span className="text-[11px] text-muted italic">streaming…</span>}
      {showActions && (
        <div
          style={{ opacity: hover ? 1 : 0, transition: "opacity 120ms ease-out" }}
          className="absolute -bottom-3 right-2 z-10 flex items-center gap-1 rounded-full border border-border bg-surface px-1 py-0.5 shadow-sm"
        >
          <button
            type="button"
            onClick={() => copyTo("text")}
            className="p-1 rounded-full text-muted hover:text-accent"
            aria-label="Copy message"
            data-tooltip="Copy message"
          >
            {copied === "text" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Copy size={12} />
            )}
          </button>
          <button
            type="button"
            onClick={() => copyTo("md")}
            className="p-1 rounded-full text-muted hover:text-accent"
            aria-label="Copy raw markdown"
            data-tooltip="Copy raw markdown"
          >
            {copied === "md" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Code2 size={12} />
            )}
          </button>
        </div>
      )}
    </div>
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
