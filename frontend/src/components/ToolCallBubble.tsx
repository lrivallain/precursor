import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  AlertCircle,
  ArrowUpRight,
  FileText,
  Workflow,
  Wrench,
} from "lucide-react";
import { openWorkspaceFile, toWorkspaceFileLink } from "../lib/workspaceLink";
import type { WorkspaceFileRef } from "../lib/workspaceLink";

interface Props {
  name: string;
  arguments: string;
  content: string | null;
  isError?: boolean;
  pending?: boolean;
  /** Workspace file this call touched, from the tool call's metadata. */
  link?: WorkspaceFileRef | null;
}

function tryPrettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

function splitName(qualified: string): { server: string; tool: string } {
  const idx = qualified.indexOf("__");
  if (idx === -1) return { server: "", tool: qualified };
  return { server: qualified.slice(0, idx), tool: qualified.slice(idx + 2) };
}

export function ToolCallBubble({
  name,
  arguments: args,
  content,
  isError,
  pending,
  link: linkRef,
}: Props) {
  const [open, setOpen] = useState(false);
  const { server, tool } = splitName(name);
  // A successful workspace read/write links straight to the file it touched.
  const link = useMemo(
    () => (pending || isError ? null : toWorkspaceFileLink(linkRef)),
    [linkRef, pending, isError],
  );

  return (
    <div className="w-full">
      <div
        className={`border rounded-lg text-sm ${
          isError
            ? "border-red-500/40 bg-red-500/5"
            : "border-blue-500/40 bg-blue-500/5"
        }`}
      >
        <div className="flex items-center rounded-lg hover:bg-blue-500/10">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="flex-1 min-w-0 flex items-center gap-2 px-3 py-2 text-left"
          >
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {isError ? (
              <AlertCircle size={14} className="text-red-500" />
            ) : (
              <Wrench size={14} className="text-blue-500" />
            )}
            <span className="text-[11px] uppercase tracking-wide text-blue-500/80">
              tool{server && ` · ${server}`}
            </span>
            <span className="font-mono text-xs truncate">{tool}</span>
            {pending && (
              <span className="ml-auto text-[11px] text-blue-500 italic">running…</span>
            )}
            {!pending && isError && (
              <span className="ml-auto text-[11px] text-red-500">error</span>
            )}
          </button>
          {link && (
            <button
              type="button"
              onClick={() => openWorkspaceFile(link.slug, link.path)}
              className="group mr-3 shrink-0 inline-flex cursor-pointer items-center gap-1 rounded-full border border-blue-500/40 bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-600 hover:bg-blue-500/20 dark:text-blue-300"
              data-tooltip={`Open ${link.path} in Files`}
            >
              {link.isDiagram ? <Workflow size={11} /> : <FileText size={11} />}
              <span className="max-w-[14rem] truncate">{link.name}</span>
              <ArrowUpRight
                size={11}
                className="opacity-60 transition group-hover:opacity-100"
              />
            </button>
          )}
        </div>
        {open && (
          <div className="px-3 pb-3 space-y-2 border-t border-blue-500/30">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-blue-500/80 mb-1 mt-2">
                Arguments
              </div>
              <pre className="text-xs bg-bg/60 border border-blue-500/20 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words">
                {tryPrettyJson(args || "{}")}
              </pre>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-blue-500/80 mb-1">
                Result
              </div>
              {pending ? (
                <div className="text-xs text-muted italic">Waiting for result…</div>
              ) : (
                <pre className="text-xs bg-bg/60 border border-blue-500/20 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words max-h-72">
                  {content ?? "(no content)"}
                </pre>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
