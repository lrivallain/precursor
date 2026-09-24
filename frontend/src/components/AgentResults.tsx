import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  CornerDownRight,
  ExternalLink,
  History,
  Link2,
  Package,
} from "lucide-react";
import { api } from "../lib/api";
import { normalizeArtifactMarkdown, type AgentArtifactDirective } from "../lib/directives";
import type { ResultVersion } from "../lib/agentResults";
import type { AgentArtifact, AgentSession } from "../lib/types";
import { Markdown } from "./Markdown";
import { formatTimestamp } from "./MessageMeta";

export type AgentPane = "result" | "activity";

// Every kind renders as Markdown: JSON is fenced (pretty-printed when it
// parses), text and markdown pass through.
function artifactMarkdown(kind: string, content: string): string {
  if (kind !== "json") return normalizeArtifactMarkdown(content);
  try {
    return `\`\`\`json\n${JSON.stringify(JSON.parse(content), null, 2)}\n\`\`\``;
  } catch {
    return `\`\`\`\n${content}\n\`\`\``;
  }
}

function artifactPermalink(agent: AgentSession, artifact: AgentArtifact): string {
  const ref = agent.public_id ?? String(agent.id);
  return `${window.location.origin}/agents/${encodeURIComponent(ref)}?artifact=${artifact.id}`;
}

function lineCount(content: string): number {
  return content.split("\n").filter((l) => l.trim()).length;
}

/**
 * The Result / Activity switch over the agent's main pane. Only mounted once
 * the agent has published something — an agent with no result has nothing to
 * switch to, so it keeps the plain timeline.
 */
export function AgentPaneTabs({
  pane,
  onChange,
  latest,
  live,
}: {
  pane: AgentPane;
  onChange: (pane: AgentPane) => void;
  /** Newest version number, shown on the Result tab. */
  latest: number;
  /** A turn is in flight — flagged on the Activity tab. */
  live: boolean;
}) {
  const tab = (id: AgentPane, label: string, icon: ReactNode, extra: ReactNode) => {
    const active = pane === id;
    return (
      <button
        type="button"
        role="tab"
        id={`agent-pane-${id}`}
        aria-selected={active}
        aria-controls={`agent-panel-${id}`}
        onClick={() => onChange(id)}
        className={`-mb-px flex items-center gap-1.5 border-b-2 px-2.5 py-2 text-[12px] font-medium transition ${
          active
            ? "border-accent text-text"
            : "border-transparent text-muted hover:border-border hover:text-text"
        }`}
      >
        {icon}
        {label}
        {extra}
      </button>
    );
  };
  return (
    <div
      role="tablist"
      aria-label="Agent view"
      className="flex shrink-0 items-center gap-1 border-b border-border px-5"
    >
      {tab(
        "result",
        "Result",
        <Package size={13} />,
        <span className="rounded-full bg-emerald-500/15 px-1.5 text-[10px] tabular-nums text-emerald-700 dark:text-emerald-300">
          v{latest}
        </span>,
      )}
      {tab(
        "activity",
        "Activity",
        <Activity size={13} />,
        live ? (
          <span className="relative flex h-2 w-2" aria-label="Working">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
          </span>
        ) : null,
      )}
    </div>
  );
}

// One published output, rendered as a document: a title row with its sharing
// actions, then the body at reading size.
function ResultDocument({ agent, artifact }: { agent: AgentSession; artifact: AgentArtifact }) {
  const [copied, setCopied] = useState<null | "content" | "link">(null);
  const body = useMemo(
    () => artifactMarkdown(artifact.kind, artifact.content),
    [artifact.kind, artifact.content],
  );

  const copy = useCallback(
    async (what: "content" | "link") => {
      try {
        await navigator.clipboard.writeText(
          what === "link" ? artifactPermalink(agent, artifact) : artifact.content,
        );
        setCopied(what);
        window.setTimeout(() => setCopied((c) => (c === what ? null : c)), 1200);
      } catch {
        // Clipboard may be unavailable (insecure context); fail silently.
      }
    },
    [agent, artifact],
  );

  const action =
    "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted hover:bg-surface hover:text-text";
  return (
    // The body brings its own headings, so the title labels the article
    // rather than competing with them in the outline.
    <article className="mt-5 first:mt-0" aria-label={artifact.title}>
      <header className="flex items-center gap-2 border-b border-border pb-2">
        <Package size={15} className="shrink-0 text-emerald-500" />
        <p className="min-w-0 flex-1 truncate text-sm font-semibold text-text">
          {artifact.title}
        </p>
        <div className="flex shrink-0 items-center gap-0.5">
          {artifact.kind !== "link" && (
            <button type="button" onClick={() => void copy("content")} className={action}>
              {copied === "content" ? (
                <Check size={12} className="text-emerald-500" />
              ) : (
                <Copy size={12} />
              )}
              {copied === "content" ? "Copied" : "Copy"}
            </button>
          )}
          <button type="button" onClick={() => void copy("link")} className={action}>
            {copied === "link" ? <Check size={12} className="text-emerald-500" /> : <Link2 size={12} />}
            {copied === "link" ? "Copied" : "Link"}
          </button>
          <a
            href={api.agents.rawArtifactUrl(agent.id, artifact.id)}
            target="_blank"
            rel="noreferrer"
            className={action}
          >
            <ExternalLink size={12} />
            Raw
          </a>
        </div>
      </header>
      {artifact.kind === "link" ? (
        <a
          href={artifact.content.trim()}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex items-center gap-1.5 break-all text-sm font-medium text-accent hover:underline"
        >
          <ExternalLink size={14} className="shrink-0" />
          {artifact.content.trim()}
        </a>
      ) : (
        <Markdown className="mt-3 text-sm leading-relaxed text-text">{body}</Markdown>
      )}
    </article>
  );
}

/**
 * The agent's result, one version at a time.
 *
 * Opens on the newest version; the rail above it lists every earlier one,
 * newest first, so refining a deliverable never buries the current copy under
 * the drafts that preceded it. Each version carries the prompt that produced
 * it and the agent's own summary of what changed, which is what tells two
 * similar-looking versions apart.
 */
export function AgentResultView({
  agent,
  versions,
  shown,
  onSelect,
  onShowInActivity,
}: {
  agent: AgentSession;
  versions: ResultVersion[];
  /** The version on screen. */
  shown: ResultVersion;
  /** Pick a version; null follows the newest. */
  onSelect: (version: ResultVersion | null) => void;
  /** Jump to the exchange that produced a version in the timeline. */
  onShowInActivity: (exchangeIndex: number) => void;
}) {
  const latest = versions[versions.length - 1] ?? shown;
  const version = shown;
  const isLatest = version.n === latest.n;
  const when = formatTimestamp(version.at);
  const exchange = version.exchange;

  return (
    <div className="mx-auto w-full max-w-3xl px-5 py-4">
      {versions.length > 1 && (
        <div className="mb-3 flex items-center gap-2">
          <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted">
            <History size={12} /> Versions
          </span>
          <div role="group" aria-label="Result versions" className="flex min-w-0 gap-1 overflow-x-auto">
            {[...versions].reverse().map((v) => {
              const active = v.n === version.n;
              const newest = v.n === latest.n;
              return (
                <button
                  key={v.n}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onSelect(newest ? null : v)}
                  data-tooltip={`${v.artifacts.map((a) => a.title).join("\n")}\n${
                    formatTimestamp(v.at) ?? ""
                  }`}
                  className={`flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition ${
                    active
                      ? "border-accent/60 bg-accent/15 font-medium text-accent"
                      : "border-border text-muted hover:border-accent/40 hover:text-text"
                  }`}
                >
                  v{v.n}
                  {newest && (
                    <span className="rounded bg-emerald-500/15 px-1 text-[9px] font-medium uppercase tracking-wide text-emerald-700 dark:text-emerald-300">
                      Latest
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Where this version came from: the prompt behind it and the agent's own
          account of the change. */}
      <div className="rounded-lg border border-border bg-surface/50 px-3 py-2">
        <div className="flex items-center gap-1.5 text-[11px] text-muted">
          <CornerDownRight size={12} className="shrink-0" />
          <span className="min-w-0 flex-1 truncate">
            <span className="font-medium text-text">v{version.n}</span>
            {" · "}
            {version.n === 1 ? "from the task" : "refined after your follow-up"}
            {when ? ` · ${when}` : ""}
          </span>
          {exchange && (
            <button
              type="button"
              onClick={() => onShowInActivity(exchange.index)}
              className="shrink-0 rounded px-1.5 py-0.5 font-medium text-accent hover:bg-accent/10"
            >
              Show in activity
            </button>
          )}
        </div>
        {exchange?.prompt && (
          <p
            className="mt-1 line-clamp-2 whitespace-pre-wrap text-[12px] text-text"
            data-tooltip={exchange.prompt.length > 160 ? exchange.prompt : undefined}
          >
            {exchange.prompt}
          </p>
        )}
        {exchange?.summary && (
          <p className="mt-1.5 flex gap-1.5 text-[11px] leading-relaxed text-muted">
            <CheckCircle2 size={12} className="mt-0.5 shrink-0 text-emerald-500" />
            <span>{exchange.summary}</span>
          </p>
        )}
      </div>

      {!isLatest && (
        <div className="mt-3 flex items-center gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-800 dark:text-amber-200">
          <History size={13} className="shrink-0" />
          <span className="min-w-0 flex-1">
            An earlier version — superseded by v{latest.n}.
          </span>
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="shrink-0 rounded px-1.5 py-0.5 font-medium hover:bg-amber-500/20"
          >
            View latest
          </button>
        </div>
      )}

      <div className="mt-4">
        {version.artifacts.map((a) => (
          <ResultDocument key={a.id} agent={agent} artifact={a} />
        ))}
      </div>
    </div>
  );
}

/**
 * A published output, as the answer that produced it shows it: one line that
 * names it and its version, and opens it in the Result tab. The body isn't
 * repeated here — it lives, once, in the Result tab.
 */
export function ResultCard({
  artifact,
  version,
  latest,
  onOpen,
}: {
  artifact: AgentArtifact;
  version: ResultVersion;
  /** Newest version number, to say what superseded this one. */
  latest: number;
  onOpen: () => void;
}) {
  const current = version.n === latest;
  const lines = lineCount(artifact.content);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`group/result mt-2 flex w-full items-center gap-2.5 rounded-md border px-2.5 py-2 text-left transition ${
        current
          ? "border-emerald-500/40 bg-emerald-500/[0.07] hover:border-emerald-500/70"
          : "border-border bg-bg/40 opacity-80 hover:border-accent/40 hover:opacity-100"
      }`}
    >
      <Package
        size={15}
        className={`shrink-0 ${current ? "text-emerald-600 dark:text-emerald-400" : "text-muted"}`}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] font-medium text-text">{artifact.title}</span>
        <span className="block text-[10px] text-muted">
          v{version.n} · {current ? "latest result" : `superseded by v${latest}`}
          {lines > 1 ? ` · ${lines} lines` : ""}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-accent">
        Open
        <ChevronRight size={12} className="transition-transform group-hover/result:translate-x-0.5" />
      </span>
    </button>
  );
}

/**
 * An `ARTIFACT:` the message carried that isn't on the blackboard (a workflow
 * step's trace, or a publish that failed). Folded to its title so the answer
 * stays readable, but never lost.
 */
export function InlineArtifact({ artifact }: { artifact: AgentArtifactDirective }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 rounded-md border border-border bg-bg/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
      >
        <Package size={13} className="shrink-0 text-emerald-500/80" />
        <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-text">
          {artifact.title}
        </span>
        <ChevronDown
          size={12}
          className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <Markdown className="border-t border-border px-2.5 py-2 text-[12px] leading-relaxed text-text">
          {normalizeArtifactMarkdown(artifact.content)}
        </Markdown>
      )}
    </div>
  );
}
