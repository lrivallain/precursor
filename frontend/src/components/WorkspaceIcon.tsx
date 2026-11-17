import { FileCode2, FolderGit2, FolderOpen, HardDrive, Shapes } from "lucide-react";
import type { Workspace } from "../lib/types";

// The built-in workspaces lead the list and carry their own colour, icon and
// label; they can't be removed from the app.

const DEFINITIONS_HINT =
  "Definition files: agents, workflows and live summary templates\n" +
  "(*.agent.yaml, *.workflow.yaml, *.summary.yaml).\n" +
  "Opening one shows its check under the editor. The assistant's file tools can only read here.\n" +
  "Settings → Workflows migrates your agents and workflows into files.";

const CANVAS_HINT =
  "Conversation canvases: one folder per conversation (named after its id).\n" +
  "The assistant draws them with its canvas tools; they show inline in its replies.";

// The canvas colour, shared by the Canvas workspace and the canvas blocks in
// conversations so both read as the same thing.
export const CANVAS_COLOR = "text-fuchsia-500";

export function isBuiltinWorkspace(w: Workspace): boolean {
  return w.hosts_definitions || w.hosts_canvases;
}

/** What the workspace list shows on hover, for a built-in workspace. */
export function workspaceHint(w: Workspace): string | undefined {
  if (w.hosts_definitions) return "Holds your definition files: agents, workflows and summary templates";
  if (w.hosts_canvases) return "Holds the canvases your conversations draw";
  return undefined;
}

export function WorkspaceIcon({ workspace: w }: { workspace: Workspace }) {
  if (w.hosts_definitions) return <FileCode2 size={14} className="shrink-0 text-accent opacity-90" />;
  if (w.hosts_canvases) return <Shapes size={14} className={`shrink-0 ${CANVAS_COLOR} opacity-90`} />;
  if (w.kind === "git") return <FolderGit2 size={14} className="shrink-0 opacity-70" />;
  return <HardDrive size={14} className="shrink-0 opacity-70" />;
}

/** The label at the left of a local workspace's toolbar. */
export function LocalWorkspaceLabel({ workspace: w }: { workspace: Workspace }) {
  if (w.hosts_definitions) {
    return (
      <span className="inline-flex items-center gap-1.5 text-muted" data-tooltip={DEFINITIONS_HINT}>
        <FileCode2 size={14} />
        Definition files
      </span>
    );
  }
  if (w.hosts_canvases) {
    return (
      <span className="inline-flex items-center gap-1.5 text-muted" data-tooltip={CANVAS_HINT}>
        <Shapes size={14} className={CANVAS_COLOR} />
        Conversation canvases
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-muted">
      <FolderOpen size={14} />
      Local folder
    </span>
  );
}
