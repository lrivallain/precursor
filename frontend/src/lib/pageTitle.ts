import type { SidebarMode } from "../components/Sidebar";
import { sectionLabel } from "./sections";
import type { AgentsController } from "./useAgentsController";
import type { ChatsController } from "./useChatsController";
import type { LiveSessionsController } from "./useLiveSessionsController";
import type { TopicsController } from "./useTopicsController";
import type { WorkflowsController } from "./useWorkflowsController";
import type { WorkspacesController } from "./useWorkspacesController";

export const APP_TITLE = "Precursor";

const SEPARATOR = " · ";

// The slice of each section's state that names what is on screen. Only state
// the URL also carries: a history entry keeps the title it had while current,
// so naming a URL-less state (the new-agent composer, say) would mislabel the
// overview entry it opened over.
export interface PageTitleSources {
  atHome: boolean;
  mode: SidebarMode;
  topics: Pick<TopicsController, "activeTopic" | "activeCollectionId" | "collections">;
  chats: Pick<ChatsController, "activeChat">;
  live: Pick<LiveSessionsController, "activeSession">;
  agents: Pick<AgentsController, "activeAgent">;
  workflows: Pick<WorkflowsController, "activeWorkflowId" | "workflowCollection">;
  workspaces: Pick<WorkspacesController, "activeWorkspace" | "workspaceInitialPath">;
  /** What the open plugin section reported through `SectionHost.setPageTitle`. */
  pluginItem: string | null;
}

// Most specific first, so the part that tells two tabs (or two Back/Forward
// entries) apart survives the browser truncating the title.
function join(parts: ReadonlyArray<string | null | undefined>): string {
  return [...parts, APP_TITLE]
    .map((p) => p?.trim())
    .filter((p): p is string => !!p)
    .join(SEPARATOR);
}

function basename(path: string): string {
  const segs = path.split("/").filter(Boolean);
  return segs[segs.length - 1] ?? "";
}

// The open item — and whatever scopes it — for the active section. Empty when
// the section shows its overview or start surface.
function itemParts(s: PageTitleSources): Array<string | null | undefined> {
  switch (s.mode) {
    case "topics": {
      if (s.topics.activeTopic) return [s.topics.activeTopic.title];
      const coll = s.topics.collections.find((c) => c.id === s.topics.activeCollectionId);
      return [coll?.name];
    }
    case "chats":
      return [s.chats.activeChat?.title];
    case "live":
      return [s.live.activeSession?.title];
    case "agents":
      return [s.agents.activeAgent?.title];
    case "workflows": {
      const id = s.workflows.activeWorkflowId;
      return [s.workflows.workflowCollection.workflows.find((w) => w.id === id)?.name];
    }
    case "workspaces": {
      const ws = s.workspaces.activeWorkspace;
      if (!ws) return [];
      const path = s.workspaces.workspaceInitialPath;
      return [path ? basename(path) : null, ws.name];
    }
    default:
      return [s.pluginItem];
  }
}

/** The browser tab title for what is on screen, without the unread prefix. */
export function pageTitle(s: PageTitleSources): string {
  if (s.atHome) return join(["Home"]);
  return join([...itemParts(s), sectionLabel(s.mode)]);
}
