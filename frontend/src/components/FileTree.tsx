import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  FilePlus2,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
} from "lucide-react";
import { InlineTitle } from "./InlineTitle";
import type { WorkspaceFileNode } from "../lib/types";

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

export function FileTree({
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
