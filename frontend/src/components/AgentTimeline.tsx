import type { ReactNode } from "react";
import {
  AlertTriangle,
  Brain,
  ChevronDown,
  CircleDot,
  Cog,
  FileText,
  Globe,
  ShieldQuestion,
  Sparkles,
  Terminal,
  User,
  Wrench,
} from "lucide-react";
import type { AgentEvent } from "../lib/types";

// Coarse category every event maps to. Drives one consistent color scheme so
// the same kind always looks the same across every agent conversation.
export type StepCategory =
  | "user"
  | "system"
  | "assistant"
  | "reasoning"
  | "tool"
  | "permission"
  | "error"
  | "hook"
  | "skip";

// Per-category visuals: a solid pastel fill + matching marker. Theme-aware via
// alpha so both light and dark modes stay legible.
export const CATEGORY_STYLE: Record<
  Exclude<StepCategory, "skip" | "hook">,
  { label: string; box: string; marker: string; icon: ReactNode }
> = {
  user: {
    label: "User prompt",
    box: "border-sky-500/30 bg-sky-500/15",
    marker: "border-sky-500/40 bg-sky-500/20 text-sky-600 dark:text-sky-300",
    icon: <User size={13} />,
  },
  system: {
    label: "System",
    box: "border-orange-400/30 bg-orange-400/15",
    marker: "border-orange-400/40 bg-orange-400/20 text-orange-600 dark:text-orange-300",
    icon: <Cog size={13} />,
  },
  assistant: {
    label: "Assistant",
    box: "border-border bg-surface",
    marker: "border-border bg-surface text-muted",
    icon: <Sparkles size={13} />,
  },
  reasoning: {
    label: "Thinking",
    box: "border-cyan-500/30 bg-cyan-500/10",
    marker: "border-cyan-500/40 bg-cyan-500/15 text-cyan-600 dark:text-cyan-300",
    icon: <Brain size={13} />,
  },
  tool: {
    label: "Tool",
    box: "border-violet-500/30 bg-violet-500/12",
    marker: "border-violet-500/40 bg-violet-500/20 text-violet-600 dark:text-violet-300",
    icon: <Wrench size={13} />,
  },
  permission: {
    label: "Permission",
    box: "border-orange-500/40 bg-orange-500/10",
    marker: "border-orange-500/50 bg-orange-500/15 text-orange-600 dark:text-orange-400",
    icon: <ShieldQuestion size={13} />,
  },
  error: {
    label: "Error",
    box: "border-red-500/40 bg-red-500/12",
    marker: "border-red-500/50 bg-red-500/15 text-red-500",
    icon: <AlertTriangle size={13} />,
  },
};

// Map a raw event onto a category. Unmapped kinds fall back by substring so new
// SDK event names still land somewhere sensible.
export function classify(event: AgentEvent): StepCategory {
  const kind = event.kind.toLowerCase();
  if (kind.includes("delta")) return "skip";
  // Surfaced as the global sign-in banner, not as a timeline node.
  if (kind === "mcp_auth_required") return "skip";
  if (kind === "permission_request") return "permission";
  if (event.tool_name || kind.startsWith("tool")) return "tool";
  if (kind.includes("reason") || kind.includes("think")) return "reasoning";
  if (kind.includes("error")) return "error";
  if (
    kind.includes("turn") ||
    kind.includes("usage") ||
    kind.includes("idle") ||
    kind.includes("session") ||
    kind.includes("abort")
  )
    return "hook";
  if (kind.includes("user")) return "user";
  if (kind.includes("system")) return "system";
  if (kind.includes("assistant") || kind === "message") return "assistant";
  return "hook";
}

// A tool icon keyed off the tool's name (shell/file/url) for quick scanning.
export function toolIcon(name: string | null): ReactNode {
  const n = (name ?? "").toLowerCase();
  if (n.includes("shell") || n.includes("bash") || n.includes("command"))
    return <Terminal size={13} />;
  if (n.includes("write") || n.includes("file") || n.includes("read") || n.includes("edit"))
    return <FileText size={13} />;
  if (n.includes("url") || n.includes("fetch") || n.includes("search") || n.includes("web"))
    return <Globe size={13} />;
  return <Wrench size={13} />;
}

// A "transition" hook (turn start/end, usage, idle…) rendered as a small yellow
// bubble floated to the right of the workflow — side information, out of the way.
function HookBubble({ event }: { event: AgentEvent }) {
  return (
    <div
      className="my-0.5 flex items-center gap-1 self-end rounded-full border border-yellow-400/40 bg-yellow-400/15 px-2 py-0.5 text-[9px] uppercase tracking-wide text-yellow-700 dark:text-yellow-300"
      title="Lifecycle hook"
    >
      <CircleDot size={8} className="opacity-70" />
      {event.kind.replace(/_/g, " ").replace(/data$/i, "").trim()}
    </div>
  );
}

// The centered link drawn between two consecutive workflow boxes.
function Connector() {
  return (
    <div className="group flex flex-col items-center px-6 py-0.5" aria-hidden>
      <span className="-mt-1 h-2 w-2 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
      <span className="h-3 w-0.5 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
      <ChevronDown
        size={18}
        strokeWidth={2.5}
        className="-mt-1.5 -mb-1.5 text-muted/70 transition-colors group-hover:text-accent"
      />
    </div>
  );
}

// The link between two main boxes, with any lifecycle hooks that happened in
// between floated to the right of the arrow — so the arrow always sits directly
// between the steps and the hooks never push the boxes apart.
export function StepConnector({ hooks }: { hooks: AgentEvent[] }) {
  if (hooks.length === 0) return <Connector />;
  return (
    <div className="group relative flex w-full max-w-xl justify-end py-1">
      <div
        className="absolute -inset-y-1 left-1/2 flex -translate-x-1/2 flex-col items-center"
        aria-hidden
      >
        <span className="h-2 w-2 shrink-0 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
        <span className="w-0.5 flex-1 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
        <ChevronDown
          size={18}
          strokeWidth={2.5}
          className="-mt-1.5 shrink-0 text-muted/70 transition-colors group-hover:text-accent"
        />
      </div>
      <div className="relative flex flex-col items-end gap-0.5">
        {hooks.map((ev, i) => (
          <HookBubble key={i} event={ev} />
        ))}
      </div>
    </div>
  );
}

// Hooks before the first box or after the last one, with no arrow to attach to.
export function HookGutter({ hooks }: { hooks: AgentEvent[] }) {
  return (
    <div className="flex w-full max-w-xl flex-col items-end gap-0.5 py-1">
      {hooks.map((ev, i) => (
        <HookBubble key={i} event={ev} />
      ))}
    </div>
  );
}
