import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowUp,
  Check,
  ClipboardCheck,
  ClipboardCopy,
  CloudOff,
  CloudUpload,
  Code2,
  ChevronLeft,
  Download,
  ExternalLink,
  Eye,
  FilePlus2,
  FileText,
  FileCode2,
  FolderOpen,
  FolderPlus,
  GitMerge,
  History,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  SquareCode,
  Trash2,
  Upload,
  Workflow,
} from "lucide-react";
import { api, workspaceRawUrl } from "../lib/api";
import { vscodeUrl, workspaceAbsolutePath } from "../lib/localPath";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useIsNarrow } from "../lib/useMediaQuery";
import { useConfirm } from "./ConfirmDialog";
import { Markdown } from "./Markdown";
import { ResizeHandle } from "./ResizeHandle";
import { WorkspaceChat } from "./WorkspaceChat";
import { FileTree } from "./FileTree";
import { ChangesPanel, GitDiffPane, discardPrompt } from "./GitChanges";
import type { DiffTarget } from "./GitChanges";
import { HistoryPanel } from "./GitHistory";
import { BranchPicker } from "./BranchPicker";
import { ConflictPane } from "./GitConflict";
import { DrawioEditor } from "./DrawioEditor";
import {
  DefinitionFileIssues,
  isPlaced,
  useDefinitionReport,
} from "./DefinitionFileIssues";
import type { CodeEditorHandle, EditorMarker } from "./CodeEditor";
import { lineChanges } from "../lib/diffGutter";
import type { LineChange } from "../lib/diffGutter";
import { PlainTextEditor } from "./PlainTextEditor";
import type {
  GitActionResult,
  GitConflict,
  GitFileStatus,
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
  ".drawio",
  ".drawio.xml",
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

// Extensions the drawio MCP server writes (services/mcp/drawio_server.py).
function isDrawio(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".drawio") || lower.endsWith(".drawio.xml");
}

function hasPreview(name: string): boolean {
  return isMarkdown(name) || isHtml(name) || isDrawio(name);
}

// Monaco is its own chunk, fetched the first time a file is edited.
const CodeEditor = lazy(() =>
  import("./CodeEditor")
    .then((m) => ({ default: m.CodeEditor }))
    .catch(() => ({ default: PlainTextEditor })),
);

function PaneTab({
  active,
  onSelect,
  children,
}: {
  active: boolean;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium uppercase tracking-wide ${
        active ? "bg-surface text-text" : "text-muted hover:text-text"
      }`}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}

// Which list the left pane shows in a git workspace (per browser).
const LEFT_TAB_KEY = "precursor:workspace:leftTab";
type LeftTab = "files" | "changes" | "history";
const LEFT_TABS: LeftTab[] = ["files", "changes", "history"];

const VSCODE_HINT =
  "\nOpens the server's copy, so VS Code must run on the same computer.";

function OpenInVSCode({
  path,
  label,
  size,
  position,
}: {
  /** Absolute path on the server. */
  path: string;
  label: string;
  size: number;
  /** Where the cursor is, read at click time so the link lands on that line. */
  position?: () => { line: number; column: number } | null;
}) {
  return (
    <a
      className="p-1 rounded text-muted hover:text-text hover:bg-surface"
      href={vscodeUrl(path)}
      aria-label={label}
      data-tooltip={label + VSCODE_HINT}
      onClick={(e) => {
        // The link follows its href after this handler, so it lands on the line.
        const at = position?.();
        e.currentTarget.href = vscodeUrl(path, at?.line, at?.column);
      }}
    >
      <SquareCode size={size} />
    </a>
  );
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
}: {
  workspace: Workspace;
  initialPath: string | null;
  onPathChange: (path: string | null) => void;
  onDeleted: () => void;
  onSetRole?: (roleId: number | null) => Promise<void>;
}) {
  const confirmAction = useConfirm();
  const area = workspace;
  const [files, setFiles] = useState<WorkspaceFileNode[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  // Bumped on every save, so views of the file on disk (the definition check) refresh.
  const [savedVersion, setSavedVersion] = useState(0);
  const [mode, setMode] = useState<"edit" | "preview">("preview");
  const [loadingFile, setLoadingFile] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedPath, setCopiedPath] = useState(false);
  // The working copy's absolute path on the server, for "Open in VS Code".
  const [rootPath, setRootPath] = useState<string | null>(null);
  // Monaco's cursor, for opening VS Code on the same line.
  const cursorRef = useRef<{ line: number; column: number } | null>(null);
  // The file whose editor has been shown: it then stays mounted (hidden) under
  // a preview, keeping its undo history, scroll and cursor.
  const [editorFor, setEditorFor] = useState<string | null>(null);
  const editorHandle = useRef<CodeEditorHandle | null>(null);
  const [leftTabPref, setLeftTabPref] = useState<LeftTab>(() => {
    const stored = localStorage.getItem(LEFT_TAB_KEY) as LeftTab | null;
    return stored && LEFT_TABS.includes(stored) ? stored : "files";
  });
  // History limited to one file (its repository path), from "File history".
  const [historyPath, setHistoryPath] = useState<string | null>(null);
  // The changed file whose diff fills the main area (over the editor, which
  // stays mounted underneath).
  const [diff, setDiff] = useState<DiffTarget | null>(null);
  // A pull or push git couldn't do on its own.
  const [conflict, setConflict] = useState<{ detail: string; path: string } | null>(null);
  const [gutter, setGutter] = useState<LineChange[]>([]);
  // Bumped when the branch changes: the editor starts afresh, so no undo step
  // can bring the other branch's text back into the buffer.
  const [editorGeneration, setEditorGeneration] = useState(0);
  // Inline create-in-tree state (VS Code style): an input row appears at the
  // target parent ("" = root) until the user confirms or cancels. No modal.
  const [pendingCreate, setPendingCreate] = useState<{
    kind: "file" | "folder";
    parent: string;
  } | null>(null);

  const dirty = content !== savedContent;
  const isGit = area.kind !== "local";
  const leftTab: LeftTab = isGit ? leftTabPref : "files";
  function setLeftTab(tab: LeftTab): void {
    setLeftTabPref(tab);
    localStorage.setItem(LEFT_TAB_KEY, tab);
  }
  // The open file as git names it (relative to the repository, not the subdir).
  const subdir = (area.subdir ?? "").replace(/^\/+|\/+$/g, "");
  const repoPath = isGit && activePath ? (subdir ? `${subdir}/${activePath}` : activePath) : null;
  const merging = status?.merging ?? false;
  const conflictCount = status?.files.filter((f) => f.conflicted).length ?? 0;
  const activeConflicted =
    repoPath !== null && !!status?.files.some((f) => f.path === repoPath && f.conflicted);
  const showPreview = activePath !== null && mode === "preview" && hasPreview(activePath);
  const editorVisible =
    activePath !== null && !loadingFile && isEditable(activePath) && !showPreview;

  useEffect(() => {
    if (editorVisible) setEditorFor(activePath);
  }, [editorVisible, activePath]);

  const definitionReport = useDefinitionReport(area.id, activePath, savedVersion);
  const markers = useMemo<EditorMarker[]>(
    () =>
      (definitionReport?.in_definitions ? definitionReport.issues : [])
        .filter(isPlaced)
        .map((i) => ({
          severity: i.severity,
          message: i.location ? `${i.message} (${i.location})` : i.message,
          line: i.line,
          column: i.column,
          endLine: i.end_line,
          endColumn: i.end_column,
        })),
    [definitionReport],
  );

  // Resizable Files panel (left). Width persists per browser.
  const { width: filesWidth, onMouseDown: onFilesResize } = useResizableWidth({
    storageKey: "precursor:workspace:filesWidth",
    defaultWidth: 240,
    min: 160,
    max: 520,
  });

  // The tree and the editor can't share a phone screen, so they become a
  // list → detail flow: the tree owns the width until a file is opened, and
  // the editor's back button returns to it.
  const narrow = useIsNarrow();
  const detailOpen = activePath !== null || diff !== null;
  const showFiles = !narrow || !detailOpen;
  const showEditor = !narrow || detailOpen;

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

  // Ask the remote what it has, so ahead/behind are real: on open, and on
  // Refresh. Changes no file.
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const fetchRemote = useCallback(async () => {
    if (area.kind === "local") return;
    setFetching(true);
    try {
      const res = await api.workspaces.gitFetch(area.id);
      if (res.status) setStatus(res.status);
      setFetchError(res.ok ? null : res.detail);
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setFetching(false);
    }
  }, [area.id, area.kind]);

  useEffect(() => {
    setFetchError(null);
    setDiff(null);
    setHistoryPath(null);
    setConflict(null);
    void fetchRemote();
  }, [fetchRemote]);

  // The diff follows the file's status; it closes once the file has no
  // changes left (committed, discarded).
  useEffect(() => {
    setDiff((open) => {
      if (open?.kind === "conflict") {
        const now = status?.files.find((f) => f.path === open.file.path);
        return now?.conflicted ? open : null;
      }
      if (open?.kind !== "working") return open;
      const now = status?.files.find((f) => f.path === open.file.path);
      if (!now) return null;
      return now.code === open.file.code && now.orig_path === open.file.orig_path
        ? open
        : { kind: "working", file: now };
    });
  }, [status]);

  // Change bars in the editor's gutter, against HEAD: on open and whenever the
  // status is refreshed (after a save, a pull, a commit…).
  useEffect(() => {
    if (!repoPath || !status?.files.some((f) => f.path === repoPath)) {
      setGutter([]);
      return;
    }
    let cancelled = false;
    api.workspaces
      .gitDiff(area.id, repoPath)
      .then((d) => {
        if (!cancelled) setGutter(d.binary ? [] : lineChanges(d.diff));
      })
      .catch(() => {
        if (!cancelled) setGutter([]);
      });
    return () => {
      cancelled = true;
    };
  }, [area.id, repoPath, status]);

  useEffect(() => {
    let cancelled = false;
    setRootPath(null);
    api.workspaces
      .localPath(area.id)
      .then(({ path }) => {
        if (!cancelled) setRootPath(path);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [area.id]);

  // Open the file named in the URL once the tree has loaded. Keyed off the
  // currently-open file rather than a one-shot flag, so a *new* deep link —
  // e.g. the workspace-file chip on a tool call in a chat — still opens while
  // this view stays mounted.
  useEffect(() => {
    if (!initialPath || files.length === 0 || loadingFile) return;
    if (initialPath === activePath) return;
    if (files.some((f) => f.path === initialPath && f.type !== "dir")) {
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
      cursorRef.current = null;
      setDiff(null);
      setActivePath(path);
      onPathChange(path);
      setContent(f.content);
      setSavedContent(f.content);
      // A file with conflict markers opens where they can be resolved.
      const inRepo = subdir ? `${subdir}/${path}` : path;
      const conflictedFile = !!status?.files.some((f) => f.path === inRepo && f.conflicted);
      setMode(hasPreview(path) && !conflictedFile ? "preview" : "edit");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingFile(false);
    }
  }

  // `text` is the editor's buffer on Cmd/Ctrl+S, which may be a keystroke
  // ahead of `content`.
  async function save(text: string = content): Promise<void> {
    if (!activePath || saving || text === savedContent) return;
    setSaving(true);
    setError(null);
    try {
      await api.workspaces.writeFile(area.id, activePath, text);
      setContent(text);
      setSavedContent(text);
      setSavedVersion((v) => v + 1);
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
      const root = rootPath ?? (await api.workspaces.localPath(area.id)).path;
      // Tree paths are relative to the subdir, not the working copy.
      const full = workspaceAbsolutePath(root, area.subdir, path);
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

  // Files may have changed under git's hands: reload the tree, the status and
  // the open file.
  async function afterGitChange(): Promise<void> {
    await refreshFiles();
    await refreshStatus();
    if (activePath) {
      try {
        const f = await api.workspaces.readFile(area.id, activePath);
        setContent(f.content);
        setSavedContent(f.content);
      } catch {
        setActivePath(null);
        onPathChange(null);
        setContent("");
        setSavedContent("");
      }
    }
  }

  // Switch or create a branch. An unsaved buffer is given up first (with the
  // user's say-so), so nothing from one branch can be saved onto another.
  async function changeBranch(run: () => Promise<GitActionResult>): Promise<boolean> {
    return rewriteTree(run, "Discard unsaved changes? Switching branches reloads the open file.");
  }

  // An operation that rewrites files (switch, merge, abort): the open file is
  // reloaded afterwards, and the editor starts afresh.
  async function rewriteTree(
    run: () => Promise<GitActionResult>,
    unsavedQuestion: string,
  ): Promise<boolean> {
    if (
      dirty &&
      !(await confirmAction({
        message: unsavedQuestion,
        confirmLabel: "Discard changes",
        variant: "warning",
      }))
    )
      return false;
    setError(null);
    setConflict(null);
    try {
      await run();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    }
    setDiff(null);
    await afterGitChange();
    // After the reload, so the fresh editor starts from the new branch's text
    // with an empty undo stack.
    setEditorGeneration((g) => g + 1);
    void fetchRemote();
    return true;
  }

  async function commitFiles(
    message: string,
    paths: string[],
    andPush: boolean,
  ): Promise<GitActionResult> {
    setError(null);
    setConflict(null);
    try {
      const res = andPush
        ? await api.workspaces.gitCommitPush(area.id, message, paths)
        : await api.workspaces.gitCommit(area.id, message, paths);
      if (!res.ok) {
        if (res.needs_manual_merge) setConflict({ detail: res.detail, path: res.local_path ?? "" });
        else setError(res.detail);
      }
      await afterGitChange();
      return res;
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      setError(detail);
      return { ok: false, detail, needs_manual_merge: false, local_path: null, status: null };
    }
  }

  async function discardFile(file: GitFileStatus): Promise<void> {
    if (!(await confirmAction(discardPrompt(file)))) return;
    setError(null);
    try {
      await api.workspaces.gitDiscard(area.id, file.path);
      await afterGitChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // The tree's path for a repository path; null outside the subdir.
  function browsePathOf(repo: string): string | null {
    if (!subdir) return repo;
    return repo.startsWith(`${subdir}/`) ? repo.slice(subdir.length + 1) : null;
  }

  async function mergeRemote(): Promise<void> {
    let stopped = false;
    const done = await rewriteTree(async () => {
      const res = await api.workspaces.gitMerge(area.id);
      stopped = !res.ok;
      return res;
    }, "Discard unsaved changes? Merging may change the open file.");
    // Stopped on conflicts: they are listed in the Changes tab.
    if (done && stopped) setLeftTab("changes");
  }

  async function completeMerge(): Promise<void> {
    setError(null);
    try {
      await api.workspaces.gitMergeComplete(area.id);
      await afterGitChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function abortMerge(): Promise<void> {
    if (
      !(await confirmAction({
        message:
          "Abort the merge? Your branch and files go back to how they were before it, " +
          "and the conflicts you resolved so far are lost.",
        confirmLabel: "Abort merge",
        variant: "danger",
      }))
    )
      return;
    await rewriteTree(
      () => api.workspaces.gitMergeAbort(area.id),
      "Discard unsaved changes? Aborting the merge reloads the open file.",
    );
  }

  // Mark a conflict resolved as it is saved (the server refuses while markers remain).
  async function markResolved(path: string): Promise<void> {
    setError(null);
    try {
      setStatus(await api.workspaces.gitResolve(area.id, path));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function keepSide(
    file: GitFileStatus,
    side: "ours" | "theirs",
    conflict: GitConflict,
  ): Promise<void> {
    const present = side === "ours" ? conflict.has_ours : conflict.has_theirs;
    const mine = side === "ours";
    const message = present
      ? `Keep ${mine ? "your" : "the incoming"} version of "${file.path}"? ` +
        `${mine ? "The incoming" : "Your"} changes to it are dropped.`
      : `Delete "${file.path}", as ${mine ? "your" : "the incoming"} branch did? ` +
        `${mine ? "The incoming" : "Your"} changes to it are dropped.`;
    if (
      !(await confirmAction({
        message,
        confirmLabel: present ? "Keep this version" : "Delete file",
        variant: present ? "warning" : "danger",
      }))
    )
      return;
    setError(null);
    try {
      await api.workspaces.gitResolve(area.id, file.path, side);
      await afterGitChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // "Open file" from a diff, when the file is in the tree now.
  function diffOpenTarget(target: DiffTarget): (() => void) | undefined {
    const repo = target.file.path;
    const browse =
      target.kind === "commit" ? browsePathOf(repo) : (target.file.browse_path ?? null);
    if (!browse || !files.some((f) => f.path === browse && f.type !== "dir")) return undefined;
    return () => openFromChanges(browse);
  }

  function showFileHistory(): void {
    if (!repoPath) return;
    setHistoryPath(repoPath);
    setLeftTab("history");
    if (narrow) {
      setDiff(null);
      setActivePath(null);
    }
  }

  function openFromChanges(browsePath: string): void {
    if (browsePath === activePath) setDiff(null);
    else void openFile(browsePath);
  }

  return (
    <div className="h-full flex flex-col">
      {isGit ? (
        <GitBar
          area={area}
          status={status}
          dirty={dirty}
          fetching={fetching}
          fetchError={fetchError}
          onRefresh={async () => {
            await fetchRemote();
            await refreshStatus();
          }}
          onAfterSync={afterGitChange}
          onSwitchBranch={(name) => changeBranch(() => api.workspaces.gitSwitch(area.id, name))}
          onCreateBranch={(name) =>
            changeBranch(() => api.workspaces.gitCreateBranch(area.id, name))
          }
          onConflict={setConflict}
          onShowChanges={() => {
            setLeftTab("changes");
            // On a phone the list is its own screen.
            if (narrow) {
              setDiff(null);
              setActivePath(null);
            }
          }}
          onDeleted={onDeleted}
          onError={setError}
          rootPath={rootPath}
        />
      ) : (
        <LocalWorkspaceBar
          area={area}
          onDeleted={onDeleted}
          onError={setError}
          rootPath={rootPath}
        />
      )}

      {error && (
        <div className="px-4 py-2 text-sm bg-red-500/10 text-red-500 border-b border-border">
          {error}
        </div>
      )}

      {conflict && !merging && (
        <div className="px-4 py-3 text-sm bg-amber-500/10 border-b border-border space-y-1.5">
          <p className="font-medium text-amber-600 dark:text-amber-400">
            Your branch and the remote have both moved on.
          </p>
          <p className="text-muted">
            Merge the remote&apos;s commits into yours: if the same lines changed on both
            sides, you&apos;ll resolve those conflicts here before the merge is committed.
          </p>
          <details className="text-xs text-muted">
            <summary className="cursor-pointer">What git said</summary>
            <pre className="mt-1 whitespace-pre-wrap font-mono">{conflict.detail}</pre>
          </details>
          <div className="flex items-center gap-3">
            <button
              className="inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1 text-xs text-white"
              onClick={() => void mergeRemote()}
            >
              <GitMerge size={13} /> Merge remote changes
            </button>
            <button
              className="text-xs underline text-muted hover:text-text"
              onClick={() => setConflict(null)}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {merging && (
        <div
          className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2 text-sm bg-amber-500/10 border-b border-border"
          role="status"
        >
          <GitMerge size={15} className="shrink-0 text-amber-600 dark:text-amber-400" />
          <span className="min-w-0 flex-1">
            <span className="font-medium">Merge in progress</span>
            <span className="text-muted">
              {conflictCount > 0
                ? ` — ${conflictCount} conflict${conflictCount === 1 ? "" : "s"} left to resolve.`
                : " — every conflict is resolved."}
            </span>
          </span>
          {conflictCount > 0 && leftTab !== "changes" && (
            <button
              className="text-xs underline text-muted hover:text-text"
              onClick={() => setLeftTab("changes")}
            >
              Show conflicts
            </button>
          )}
          <button
            className="rounded border border-border px-2.5 py-1 text-xs hover:bg-surface"
            onClick={() => void abortMerge()}
          >
            Abort merge
          </button>
          <button
            className="rounded bg-accent px-2.5 py-1 text-xs text-white disabled:opacity-50"
            disabled={conflictCount > 0}
            data-tooltip={conflictCount > 0 ? "Resolve every conflict first" : "Commit the merge"}
            onClick={() => void completeMerge()}
          >
            Complete merge
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 flex">
        {/* Hidden rather than unmounted on a phone while a file or diff is
            open, so the commit message, the expanded commit and the scroll
            position are still there on the way back. */}
        <aside
          className={`relative border-r border-border flex flex-col min-h-0 ${
            // Beside the assistant's 2.25rem rail, so it fills what's left
            // rather than claiming a full viewport width the row can't afford.
            narrow ? "flex-1 min-w-0 border-r-0" : "shrink-0"
          } ${showFiles ? "" : "hidden"}`}
          style={narrow ? undefined : { width: filesWidth }}
        >
          {!narrow && <ResizeHandle onMouseDown={onFilesResize} side="right" />}
          <div className="flex items-center justify-between px-3 h-10 border-b border-border">
            {isGit ? (
              <div role="tablist" aria-label="Workspace panes" className="-ml-1.5 flex items-center gap-0.5">
                <PaneTab active={leftTab === "files"} onSelect={() => setLeftTab("files")}>
                  Files
                </PaneTab>
                <PaneTab active={leftTab === "changes"} onSelect={() => setLeftTab("changes")}>
                  Changes
                  {(status?.files.length ?? 0) > 0 && (
                    <span className="ml-1 rounded bg-accent/15 px-1 text-[10px] leading-4 text-accent">
                      {status?.files.length}
                    </span>
                  )}
                </PaneTab>
                <PaneTab
                  active={leftTab === "history"}
                  onSelect={() => {
                    // The tab itself shows the whole branch; File history narrows it.
                    if (leftTab !== "history") setHistoryPath(null);
                    setLeftTab("history");
                  }}
                >
                  History
                </PaneTab>
              </div>
            ) : (
              <span className="text-xs font-medium text-muted uppercase tracking-wide">
                Files
              </span>
            )}
            {leftTab === "files" && (
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
            )}
          </div>
          {leftTab === "history" ? (
            <div className="flex-1 min-h-0">
              <HistoryPanel
                key={`${area.id}:${historyPath ?? ""}`}
                workspaceId={area.id}
                head={status?.head ?? null}
                path={historyPath}
                onClearPath={() => setHistoryPath(null)}
                activeKey={diff?.kind === "commit" ? `${diff.commit.sha}:${diff.file.path}` : null}
                onOpenDiff={(commit, file) => setDiff({ kind: "commit", commit, file })}
              />
            </div>
          ) : leftTab === "changes" ? (
            <div className="flex-1 min-h-0">
              <ChangesPanel
                files={status?.files ?? []}
                activeDiff={diff?.kind === "working" || diff?.kind === "conflict" ? diff.file.path : null}
                canPush={!status?.detached}
                merging={merging}
                onOpenConflict={(file) => setDiff({ kind: "conflict", file })}
                onOpenDiff={(file) => setDiff({ kind: "working", file })}
                onOpenFile={openFromChanges}
                onDiscard={discardFile}
                onCommit={(message, paths) => commitFiles(message, paths, false)}
                onCommitPush={(message, paths) => commitFiles(message, paths, true)}
              />
            </div>
          ) : (
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
          )}
        </aside>

        {showEditor && (
        <section className="flex-1 min-w-0 flex flex-col">
          {diff?.kind === "conflict" ? (
            <ConflictPane
              workspaceId={area.id}
              file={diff.file}
              narrow={narrow}
              version={status}
              onResolveInEditor={diffOpenTarget(diff)}
              onMarkResolved={() => markResolved(diff.file.path)}
              onKeep={(side, info) => keepSide(diff.file, side, info)}
              onClose={() => setDiff(null)}
            />
          ) : diff ? (
            <GitDiffPane
              workspaceId={area.id}
              target={diff}
              narrow={narrow}
              version={status}
              onOpenFile={diffOpenTarget(diff)}
              onClose={() => setDiff(null)}
            />
          ) : null}
          <div className={diff ? "hidden" : "flex min-h-0 flex-1 flex-col"}>
          {activePath ? (
            <>
              {/* Wraps on phones: fixed-height nowrap would clip the
                  edit/preview toggle and the file actions off-screen. */}
              <div className="flex flex-wrap items-center gap-2 px-4 py-1.5 border-b border-border md:h-10 md:flex-nowrap md:py-0">
                {narrow && (
                  <button
                    type="button"
                    className="-ml-2 shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-text"
                    aria-label="Back to files"
                    onClick={() => setActivePath(null)}
                  >
                    <ChevronLeft size={16} />
                  </button>
                )}
                <FileText size={15} className="text-muted shrink-0" />
                <span className="text-sm truncate flex-1 min-w-0" title={activePath}>
                  {activePath}
                  {dirty && <span className="text-accent"> •</span>}
                </span>
                {hasPreview(activePath) && (
                  <div className="flex shrink-0 rounded border border-border overflow-hidden text-xs">
                    <button
                      className={`px-2 py-1 inline-flex items-center gap-1 ${
                        mode === "edit" ? "bg-surface" : "hover:bg-surface/60"
                      }`}
                      onClick={() => setMode("edit")}
                    >
                      {isDrawio(activePath) ? (
                        <>
                          <Code2 size={13} /> XML
                        </>
                      ) : (
                        <>
                          <Pencil size={13} /> Edit
                        </>
                      )}
                    </button>
                    <button
                      className={`px-2 py-1 inline-flex items-center gap-1 ${
                        mode === "preview" ? "bg-surface" : "hover:bg-surface/60"
                      }`}
                      onClick={() => setMode("preview")}
                    >
                      {isDrawio(activePath) ? (
                        <>
                          <Workflow size={13} /> Diagram
                        </>
                      ) : (
                        <>
                          <Eye size={13} /> Preview
                        </>
                      )}
                    </button>
                  </div>
                )}
                <button
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
                  disabled={!dirty || saving}
                  onClick={() => void save()}
                >
                  {saving ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Save size={13} />
                  )}
                  Save
                </button>
                {activeConflicted && repoPath && (
                  <button
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-border text-xs hover:bg-surface disabled:opacity-50"
                    disabled={dirty}
                    data-tooltip={
                      dirty
                        ? "Save first"
                        : "Every conflict in this file is settled (no marker left)"
                    }
                    onClick={() => void markResolved(repoPath)}
                  >
                    <Check size={13} /> Mark resolved
                  </button>
                )}
                {isGit && (
                  <button
                    className="p-1 rounded text-muted hover:text-text hover:bg-surface"
                    aria-label="File history"
                    data-tooltip="File history: the commits that changed this file"
                    onClick={showFileHistory}
                  >
                    <History size={15} />
                  </button>
                )}
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
                {rootPath && (
                  <OpenInVSCode
                    path={workspaceAbsolutePath(rootPath, area.subdir, activePath)}
                    label="Open in VS Code"
                    size={15}
                    position={() => (editorVisible ? cursorRef.current : null)}
                  />
                )}
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
                ) : (
                  <>
                    {showPreview && isMarkdown(activePath) ? (
                      <Markdown className="text-sm leading-relaxed p-6 max-w-3xl">
                        {content || "\u200B"}
                      </Markdown>
                    ) : showPreview && isDrawio(activePath) ? (
                      <DrawioEditor
                        path={activePath}
                        xml={content}
                        onChange={setContent}
                      />
                    ) : showPreview && isHtml(activePath) ? (
                      <iframe
                        title={activePath}
                        src={workspaceRawUrl(area.slug, activePath)}
                        sandbox="allow-scripts allow-same-origin"
                        className="w-full h-full border-0 bg-white"
                      />
                    ) : null}
                    {(editorVisible || editorFor === activePath) && (
                      <div className={editorVisible ? "h-full" : "hidden"}>
                        <Suspense
                          fallback={
                            <div className="h-full flex items-center justify-center text-muted">
                              <Loader2 className="animate-spin" size={18} />
                            </div>
                          }
                        >
                          <CodeEditor
                            key={`${area.slug}/${activePath}#${editorGeneration}`}
                            modelKey={`${area.slug}/${activePath}`}
                            path={activePath}
                            value={content}
                            onChange={setContent}
                            onSave={(text) => void save(text)}
                            onCursorChange={(line, column) => {
                              cursorRef.current = { line, column };
                            }}
                            compact={narrow}
                            markers={markers}
                            lineChanges={gutter}
                            conflicts={activeConflicted}
                            handle={editorHandle}
                          />
                        </Suspense>
                      </div>
                    )}
                  </>
                )}
              </div>
              <DefinitionFileIssues
                report={definitionReport}
                onReveal={
                  editorVisible
                    ? (line, column) => editorHandle.current?.reveal(line, column)
                    : undefined
                }
              />
            </>
          ) : (
            <div className="h-full flex items-center justify-center text-muted text-sm">
              {leftTab === "history"
                ? "Select a commit to see what it changed."
                : leftTab === "changes"
                ? "Select a changed file to see what changed."
                : "Select a file to view or edit."}
            </div>
          )}
          </div>
        </section>
        )}

        <WorkspaceChat area={area} activePath={activePath} onSetRole={onSetRole} />
      </div>
    </div>
  );
}

// Keyed by the tree's paths, which are relative to the workspace's subdir.
function statusMap(status: GitStatus | null): Map<string, string> {
  const m = new Map<string, string>();
  for (const f of status?.files ?? []) {
    const path = f.browse_path === undefined ? f.path : f.browse_path;
    if (path) m.set(path, f.code);
  }
  return m;
}

// --------------------------------------------------------------------------
// Local workspace header bar (no git — just folder path + delete)
// --------------------------------------------------------------------------

function LocalWorkspaceBar({
  area,
  onDeleted,
  onError,
  rootPath,
}: {
  area: Workspace;
  onDeleted: () => void;
  onError: (msg: string | null) => void;
  rootPath: string | null;
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
      {area.hosts_definitions ? (
        <span
          className="inline-flex items-center gap-1.5 text-muted"
          data-tooltip={
            "Agent and workflow definition files (*.agent.yaml, *.workflow.yaml).\n" +
            "Opening one shows its check under the editor. The assistant's file tools can only read here.\n" +
            "Settings → Workflows migrates your agents and workflows into files."
          }
        >
          <FileCode2 size={14} />
          Agent &amp; workflow definitions
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5 text-muted">
          <FolderOpen size={14} />
          Local folder
        </span>
      )}
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
      {rootPath && (
        <OpenInVSCode
          path={rootPath}
          label="Open folder in VS Code"
          size={13}
        />
      )}
      <div className="flex-1" />
      {/* Its folder holds every agent and workflow declaration. */}
      {!area.hosts_definitions && (
        <button
          className="p-1.5 rounded hover:bg-surface text-muted hover:text-red-500"
          aria-label="Remove workspace"
          data-tooltip="Remove workspace (deletes the local folder)"
          onClick={removeArea}
        >
          <Trash2 size={14} />
        </button>
      )}
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
  fetching,
  fetchError,
  onRefresh,
  onAfterSync,
  onSwitchBranch,
  onCreateBranch,
  onConflict,
  onShowChanges,
  onDeleted,
  onError,
  rootPath,
}: {
  area: Workspace;
  status: GitStatus | null;
  dirty: boolean;
  fetching: boolean;
  fetchError: string | null;
  onRefresh: () => Promise<void>;
  onAfterSync: () => Promise<void>;
  onSwitchBranch: (name: string) => Promise<boolean>;
  onCreateBranch: (name: string) => Promise<boolean>;
  onConflict: (conflict: { detail: string; path: string } | null) => void;
  /** Show the Changes list (the review and commit). */
  onShowChanges: () => void;
  onDeleted: () => void;
  onError: (msg: string | null) => void;
  rootPath: string | null;
}) {
  const confirmAction = useConfirm();
  const [busy, setBusy] = useState<"pull" | "push" | null>(null);
  const [copied, setCopied] = useState(false);

  const changeCount = status?.files.length ?? 0;
  const detached = status?.detached ?? false;
  // Known once status has loaded; an older backend doesn't report it.
  const unpublished = status !== null && status.upstream === null && !detached;
  const ahead = status?.ahead ?? 0;
  const merging = status?.merging ?? false;

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
    onConflict(null);
    try {
      const res = await api.workspaces.gitPull(area.id);
      if (!res.ok && res.needs_manual_merge) {
        onConflict({ detail: res.detail, path: res.local_path ?? "" });
      }
      await onAfterSync();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function push(): Promise<void> {
    setBusy("push");
    onError(null);
    onConflict(null);
    try {
      const res = await api.workspaces.gitPush(area.id);
      if (!res.ok) {
        if (res.needs_manual_merge) {
          onConflict({ detail: res.detail, path: res.local_path ?? "" });
        } else {
          onError(res.detail);
        }
      }
      await onAfterSync();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
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
      {/* Wraps on phones, like the file header, rather than pushing Pull and
          Push off-screen. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 border-b border-border bg-surface/40 text-sm md:h-10 md:flex-nowrap md:py-0">
        <BranchPicker
          workspaceId={area.id}
          current={status?.branch ?? area.branch}
          detached={detached}
          onSwitch={onSwitchBranch}
          onCreate={onCreateBranch}
        />
        {unpublished && (
          <span
            className="rounded border border-border px-1.5 text-xs text-muted"
            data-tooltip="This branch isn't on the remote yet. Publish pushes it there."
          >
            not published
          </span>
        )}
        {status && (status.behind ?? 0) > 0 && (
          <span
            className="text-amber-500"
            data-tooltip="Commits on the remote you don't have yet. Pull brings them in."
          >
            ↓ {status.behind} behind
          </span>
        )}
        <span className="whitespace-nowrap text-muted">
          {changeCount > 0
            ? `${changeCount} uncommitted change${changeCount === 1 ? "" : "s"}`
            : "clean"}
        </span>
        <button
          className="p-1 rounded hover:bg-surface text-muted hover:text-text disabled:opacity-60"
          aria-label="Refresh"
          data-tooltip="Refresh (asks the remote for new commits)"
          disabled={fetching}
          onClick={() => void onRefresh()}
        >
          <RefreshCw size={13} className={fetching ? "animate-spin" : undefined} />
        </button>
        {fetchError && (
          <span
            className="text-amber-500"
            role="img"
            aria-label="Couldn't reach the remote"
            data-tooltip={`Couldn't reach the remote, so ahead/behind may be out of date:\n${fetchError}`}
          >
            <CloudOff size={13} />
          </span>
        )}
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
        {rootPath && (
          <OpenInVSCode
            path={rootPath}
            label="Open folder in VS Code"
            size={13}
          />
        )}

        <div className="flex-1" />

        <button
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-border hover:bg-surface text-xs disabled:opacity-50"
          disabled={busy !== null || detached || unpublished || merging}
          data-tooltip={
            unpublished ? "Nothing to pull: this branch isn't on the remote yet." : undefined
          }
          onClick={pull}
        >
          {busy === "pull" ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Download size={13} />
          )}
          Pull
        </button>
        {!detached && (unpublished || ahead > 0) && (
          <button
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-border hover:bg-surface text-xs text-blue-600 dark:text-blue-400 disabled:opacity-50"
            disabled={busy !== null || merging}
            data-tooltip={
              unpublished
                ? "Push this branch to the remote and track it from now on"
                : `Push ${ahead} commit${ahead === 1 ? "" : "s"} to ${status?.upstream ?? "the remote"}`
            }
            onClick={push}
          >
            {busy === "push" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : unpublished ? (
              <CloudUpload size={13} />
            ) : (
              <ArrowUp size={13} />
            )}
            {unpublished ? "Publish" : `Push ${ahead}`}
          </button>
        )}
        <button
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
          disabled={changeCount === 0 && !dirty}
          data-tooltip="Review the changed files and commit them"
          onClick={onShowChanges}
        >
          <Upload size={13} />
          Review &amp; commit
          {changeCount > 0 && (
            <span className="ml-0.5 px-1 rounded bg-white/20 text-[10px] leading-4">
              {changeCount}
            </span>
          )}
        </button>
        {!area.hosts_definitions && (
          <button
            className="p-1.5 rounded hover:bg-surface text-muted hover:text-red-500"
            aria-label="Remove workspace"
            data-tooltip="Remove workspace (keeps remote repo)"
            onClick={removeArea}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>

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
