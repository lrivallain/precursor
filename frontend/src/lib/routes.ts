import type { SidebarMode } from "../components/Sidebar";
import type { AgentSession, Chat, Collection, MeetingSession, Topic, TopicNode } from "./types";
import { topicSlugPath } from "./topicTree";

// The SPA's path-based routes. The URL path is the single source of truth for
// mode + selection; this module parses it and builds it, and `navigate` is the
// one place that writes it.
//   /                                                    → home launcher
//   /topics/<collection-slug>/<ancestor-slugs…>/<slug>   → topics
//   /t/<public-id>                                       → topic permalink
//   /chats/<slug>                                        → chats
//   /live/<slug>                                         → live meeting sessions
//   /agents/<public-id>                                  → agents
//   /workflows/<id>[/run/<n|latest>]                     → workflows
//   /ws/<slug>/<file/path>                               → workspaces
//   /<plugin-section>/<opaque…>[#hash]                   → plugin sections
// Topic slugs are globally unique, so the trailing slug alone identifies the
// item; the collection slug and the ancestor slugs make the URL readable +
// bookmarkable. `/t/<uuid>` is the immutable address — it survives renames,
// re-parenting and collection moves, and the SPA rewrites it to the readable
// form once resolved.

export interface WsRoute {
  open: boolean;
  slug: string | null;
  path: string | null;
}

/** Route state owned by a plugin section; opaque to core. */
export interface PluginRoute {
  segments: string[];
  hash: string;
}

export interface AppRoute {
  mode: SidebarMode;
  // Path segments after `/topics` — `[collection?, …ancestors, slug]`. Legacy
  // links minted before collections joined the URL simply have no collection
  // segment; the trailing slug still resolves them.
  topicPath: string[];
  // The UUID from a `/t/<public-id>` permalink.
  topicPublicId: string | null;
  chatSlug: string | null;
  liveSlug: string | null;
  // The raw agent path segment — a public UUID for new links, or a legacy
  // integer id. Resolved to an internal numeric id once the agent list loads.
  agentRef: string | null;
  // The workflow id segment (`/workflows/<id>`); null on the gallery route.
  workflowRef: number | null;
  // The run segment (`/workflows/<id>/run/<n|latest>`): a run number as a string
  // or the literal "latest" to track the live/newest run; null when absent.
  workflowRunRef: string | null;
  // For a plugin-contributed section: the path segments after its root, and the
  // raw hash. Core treats both as opaque and hands them to the section.
  pluginSegments: string[];
  pluginHash: string;
}

/**
 * Write the address bar. `pushState` by default so Back walks the change;
 * `replace` for normalising the current entry. Never fires `popstate`, so it
 * doesn't re-run the app's URL → state sync.
 */
export function navigate(url: string, { replace = false }: { replace?: boolean } = {}): void {
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
}

// Parse the current pathname into a workspace route. `/ws` opens the overlay,
// `/ws/<slug>/<file/path>` deep-links straight to a file.
export function parseWsRoute(): WsRoute {
  const segs = window.location.pathname.replace(/^\/+|\/+$/g, "").split("/");
  if (segs[0] !== "ws") return { open: false, slug: null, path: null };
  const slug = segs[1] ? decodeURIComponent(segs[1]) : null;
  const path =
    segs.length > 2 ? segs.slice(2).map(decodeURIComponent).join("/") : null;
  return { open: true, slug, path };
}

export function parseAppRoute(): AppRoute {
  const segs = window.location.pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  const base: AppRoute = {
    mode: "topics",
    topicPath: [],
    topicPublicId: null,
    chatSlug: null,
    liveSlug: null,
    agentRef: null,
    workflowRef: null,
    workflowRunRef: null,
    pluginSegments: [],
    pluginHash: "",
  };
  if (segs[0] === "ws") return { ...base, mode: "workspaces" };
  if (segs[0] === "agents") {
    return { ...base, mode: "agents", agentRef: segs[1] ? decodeURIComponent(segs[1]) : null };
  }
  if (segs[0] === "workflows") {
    const raw = segs[1] ? Number.parseInt(decodeURIComponent(segs[1]), 10) : NaN;
    // Optional `/run/<n|latest>` deep link into one run of the workflow.
    let runRef: string | null = null;
    if (segs[2] === "run" && segs[3]) {
      const seg = decodeURIComponent(segs[3]).toLowerCase();
      if (seg === "latest") runRef = "latest";
      else if (/^\d+$/.test(seg)) runRef = seg;
    }
    return {
      ...base,
      mode: "workflows",
      workflowRef: Number.isSafeInteger(raw) && raw > 0 ? raw : null,
      workflowRunRef: runRef,
    };
  }
  if (segs[0] === "chats") {
    return { ...base, mode: "chats", chatSlug: segs[1] ? decodeURIComponent(segs[1]) : null };
  }
  if (segs[0] === "live") {
    return { ...base, mode: "live", liveSlug: segs[1] ? decodeURIComponent(segs[1]) : null };
  }
  if (segs[0] === "t") {
    return {
      ...base,
      mode: "topics",
      topicPublicId: segs[1] ? decodeURIComponent(segs[1]) : null,
    };
  }
  if (segs[0] === "topics") {
    return { ...base, mode: "topics", topicPath: segs.slice(1).map(decodeURIComponent) };
  }
  // Anything left is a candidate plugin section, which owns its whole subtree:
  // core keeps the root segment as the mode and passes the rest through
  // untouched. It deliberately does *not* check the section registry — a
  // plugin's bundle is fetched asynchronously, so at first parse nothing is
  // registered yet and a deep link would be thrown away. The gating effect
  // bounces the mode to Topics once the descriptors say it isn't real.
  if (segs[0]) {
    return {
      ...base,
      mode: segs[0],
      pluginSegments: segs.slice(1).map((seg) => decodeURIComponent(seg)),
      pluginHash: window.location.hash.replace(/^#/, ""),
    };
  }
  return base;
}

/** The content-search highlight term carried in `?q=`, or "" when absent. */
export function searchTermFromUrl(): string {
  return new URLSearchParams(window.location.search).get("q") ?? "";
}

/** Section keys core ships itself; everything else can only be a plugin. */
const CORE_MODES: ReadonlySet<string> = new Set([
  "topics",
  "chats",
  "live",
  "workspaces",
  "agents",
  "workflows",
]);

/** Whether `mode` is a plugin section rather than one of core's own. */
export function isPluginMode(mode: SidebarMode): boolean {
  return !CORE_MODES.has(mode);
}

/** The home launcher lives at the root path `/` (no path segments). */
export function isHomePath(): boolean {
  return window.location.pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean).length === 0;
}

export function topicUrl(tree: TopicNode[], topic: Topic, collections: Collection[]): string {
  const segs = topicSlugPath(tree, topic.id);
  const chain = segs.length ? segs : [topic.slug];
  const collectionSlug = collections.find((c) => c.id === topic.collection_id)?.slug;
  const all = collectionSlug ? [collectionSlug, ...chain] : chain;
  return "/topics/" + all.map(encodeURIComponent).join("/");
}

/** The Topics mode's own URL when nothing is selected: `/topics/<collection>`. */
export function topicsModeUrl(collections: Collection[], collectionId: number | null): string {
  const slug = collections.find((c) => c.id === collectionId)?.slug;
  return slug ? `/topics/${encodeURIComponent(slug)}` : "/topics";
}

export function chatUrl(chat: Chat): string {
  return "/chats/" + encodeURIComponent(chat.slug);
}

export function liveUrl(session: MeetingSession | null): string {
  if (session == null) return "/live";
  return "/live/" + encodeURIComponent(session.slug);
}

// Agents are addressed by their public UUID (public_id) in the URL.
// Until the agent list has loaded we may not know the UUID yet, so fall back to
// the internal id; the URL-sync effect rewrites it to the UUID once known.
export function agentUrl(agentId: number | null, agents: AgentSession[] | null): string {
  if (agentId == null) return "/agents";
  const a = agents?.find((x) => x.id === agentId);
  const ref = a?.public_id ?? String(agentId);
  return `/agents/${encodeURIComponent(ref)}`;
}

// Resolve a URL agent segment to an internal id. A pure-integer ref is a legacy
// id; anything else is a public UUID looked up in the loaded session list.
// Returns null when a UUID can't be matched yet (agents not loaded).
export function resolveAgentRef(ref: string | null, agents: AgentSession[] | null): number | null {
  if (!ref) return null;
  if (/^\d+$/.test(ref)) return Number(ref);
  return agents?.find((a) => a.public_id === ref)?.id ?? null;
}

/** `/workflows` (gallery), `/workflows/<id>`, or `/workflows/<id>/run/<n|latest>`. */
export function workflowUrl(workflowId: number | null, runSeg: string | null = null): string {
  if (workflowId == null) return "/workflows";
  if (runSeg) return `/workflows/${workflowId}/run/${runSeg}`;
  return `/workflows/${workflowId}`;
}

/** `/ws`, `/ws/<slug>`, or `/ws/<slug>/<file/path>` for an open file. */
export function workspaceUrl(slug: string | null, filePath: string | null): string {
  let url = "/ws";
  if (slug) {
    url += `/${encodeURIComponent(slug)}`;
    if (filePath) {
      url += "/" + filePath.split("/").map(encodeURIComponent).join("/");
    }
  }
  return url;
}

// Build the URL for a plugin section from the segments it asked for. A
// section's registered id *is* its top-level segment (see lib/plugins).
export function pluginSectionUrl(mode: string, segments: string[]): string {
  const tail = segments.filter(Boolean).map(encodeURIComponent).join("/");
  return tail ? `/${mode}/${tail}` : `/${mode}`;
}
