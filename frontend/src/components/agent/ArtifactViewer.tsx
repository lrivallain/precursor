import { useEffect, useMemo } from "react";
import { Check, Copy, ExternalLink, Link2, Package, X } from "lucide-react";
import { api } from "../../lib/api";
import { useFlash } from "../../lib/useFlash";
import type { AgentArtifact, AgentSession } from "../../lib/types";
import { Markdown } from "../Markdown";

const ACTION =
  "inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] font-medium text-muted hover:bg-surface hover:text-text";

function ArtifactBody({ artifact }: { artifact: AgentArtifact }) {
  // Pretty-print JSON when the payload parses; otherwise show it verbatim.
  const pretty = useMemo(() => {
    if (artifact.kind !== "json") return artifact.content;
    try {
      return JSON.stringify(JSON.parse(artifact.content), null, 2);
    } catch {
      return artifact.content;
    }
  }, [artifact.kind, artifact.content]);
  if (artifact.kind === "link") {
    return (
      <a
        href={artifact.content.trim()}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 break-all text-sm font-medium text-accent hover:underline"
      >
        <ExternalLink size={14} className="shrink-0" />
        {artifact.content.trim()}
      </a>
    );
  }
  if (artifact.kind === "markdown") {
    return <Markdown className="text-sm leading-relaxed text-text">{artifact.content}</Markdown>;
  }
  return (
    <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-text">{pretty}</pre>
  );
}

// A modal that renders one published artifact addressably: content by kind
// (markdown/json/text/link), plus Copy content, Copy permalink, and Open raw
// affordances so the output can be shared or consumed on its own. The permalink
// is `/agents/{ref}?artifact={id}`; the raw link hits the kind-typed raw API.
export function ArtifactViewer({
  agent,
  artifact,
  onClose,
}: {
  agent: AgentSession;
  artifact: AgentArtifact;
  onClose: () => void;
}) {
  const [copied, flash] = useFlash<"content" | "link">(1500);
  const rawUrl = api.agents.rawArtifactUrl(agent.id, artifact.id);
  const ref = agent.public_id ?? String(agent.id);
  const permalink = `${window.location.origin}/agents/${encodeURIComponent(ref)}?artifact=${artifact.id}`;

  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Artifact: ${artifact.title}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-bg shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-2 border-b border-border px-4 py-3">
          <Package size={16} className="mt-0.5 shrink-0 text-teal-500" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-text">{artifact.title}</div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
              <span className="rounded bg-border/60 px-1 uppercase tracking-wide">{artifact.kind}</span>
              {artifact.key && <span className="rounded bg-border/60 px-1 font-mono">{artifact.key}</span>}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted hover:bg-surface hover:text-text"
            aria-label="Close artifact"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
          <ArtifactBody artifact={artifact} />
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-2.5">
          {artifact.kind !== "link" && (
            <button
              type="button"
              onClick={() => void navigator.clipboard?.writeText(artifact.content).then(() => flash("content"))}
              className={ACTION}
            >
              {copied === "content" ? <Check size={13} /> : <Copy size={13} />}
              {copied === "content" ? "Copied" : "Copy content"}
            </button>
          )}
          <button
            type="button"
            onClick={() => void navigator.clipboard?.writeText(permalink).then(() => flash("link"))}
            className={ACTION}
          >
            {copied === "link" ? <Check size={13} /> : <Link2 size={13} />}
            {copied === "link" ? "Copied" : "Copy link"}
          </button>
          <a href={rawUrl} target="_blank" rel="noreferrer" className={ACTION}>
            <ExternalLink size={13} />
            Open raw
          </a>
        </div>
      </div>
    </div>
  );
}
