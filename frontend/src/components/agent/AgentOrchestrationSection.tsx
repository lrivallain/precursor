import { useState, type ReactNode } from "react";
import { Check, ChevronRight, Copy, Database, ExternalLink, Link2, Package, Plus, RotateCcw, Trash2, Webhook } from "lucide-react";
import { useAgentOrchestration, type AgentOrchestration } from "../../lib/useAgentOrchestration";
import { useFlash } from "../../lib/useFlash";
import type { AgentArtifact, AgentState, AgentTrigger } from "../../lib/types";

function SectionTitle({ icon, title, count, action }: { icon: ReactNode; title: string; count?: number; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        {icon}
        <span>{title}</span>
        {count != null && count > 0 && <span className="text-[11px] text-muted">({count})</span>}
      </div>
      {action}
    </div>
  );
}

// Shared artifacts (blackboard).
function ArtifactsIndex({
  artifacts,
  artifactLabel,
  onOpenArtifact,
}: {
  artifacts: AgentArtifact[];
  artifactLabel: (a: AgentArtifact) => string | null;
  onOpenArtifact: (a: AgentArtifact) => void;
}) {
  return (
    <div className="space-y-2">
      <SectionTitle icon={<Package size={14} />} title="Artifacts" count={artifacts.length} />
      {artifacts.length === 0 ? (
        <p className="text-[11px] text-muted">
          Nothing published yet. Completed runs post their result here for downstream agents.
        </p>
      ) : (
        artifacts.slice(0, 8).map((a) => {
          const label = artifactLabel(a) ?? a.key;
          return (
            <button
              type="button"
              key={a.id}
              onClick={() => onOpenArtifact(a)}
              className="block w-full rounded border border-border bg-surface/50 px-2 py-1 text-left transition hover:border-accent/50 hover:bg-surface"
              data-tooltip={a.kind === "link" ? a.content : `Open ${a.title}`}
            >
              <div className="flex items-center gap-1.5">
                {a.kind === "link" ? (
                  <ExternalLink size={11} className="shrink-0 text-accent" />
                ) : (
                  <Link2 size={11} className="shrink-0 text-muted" />
                )}
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium">{a.title}</span>
                {label && <span className="shrink-0 rounded bg-border/60 px-1 text-[10px] text-muted">{label}</span>}
              </div>
            </button>
          );
        })
      )}
    </div>
  );
}

function StateEntry({
  entry,
  busy,
  open,
  onToggle,
  onRemove,
}: {
  entry: AgentState;
  busy: boolean;
  open: boolean;
  onToggle: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded border border-border bg-surface/50">
      <div className="flex items-center gap-1.5 px-2 py-1">
        <button
          type="button"
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
          aria-expanded={open}
          title={open ? "Hide value" : "Show value"}
        >
          <ChevronRight size={11} className={`shrink-0 text-muted transition-transform ${open ? "rotate-90" : ""}`} />
          <span className="min-w-0 flex-1 truncate font-mono text-[10px]">{entry.key}</span>
          <span className="shrink-0 text-[10px] text-muted">{entry.value.length}</span>
        </button>
        <button
          type="button"
          onClick={onRemove}
          disabled={busy}
          className="rounded p-0.5 text-muted hover:text-red-500 disabled:opacity-50"
          aria-label={`Delete state key ${entry.key}`}
        >
          <Trash2 size={12} />
        </button>
      </div>
      {open && (
        <pre className="max-h-40 overflow-auto border-t border-border px-2 py-1 font-mono text-[10px] whitespace-pre-wrap break-words text-muted">
          {entry.value || "(empty)"}
        </pre>
      )}
    </div>
  );
}

// Durable state (private cross-run scratchpad).
function StateList({ orch }: { orch: AgentOrchestration }) {
  const { state, busy } = orch;
  // One value open at a time.
  const [expanded, setExpanded] = useState<string | null>(null);
  const reset =
    state.length > 0 ? (
      <button
        type="button"
        onClick={orch.resetState}
        disabled={busy}
        className="rounded p-0.5 text-muted hover:bg-surface hover:text-red-500 disabled:opacity-50"
        data-tooltip="Reset saved state"
        aria-label="Reset saved state"
      >
        <RotateCcw size={14} />
      </button>
    ) : null;
  return (
    <div className="space-y-2">
      <SectionTitle icon={<Database size={14} />} title="State" count={state.length} action={reset} />
      {state.length === 0 ? (
        <p className="text-[11px] text-muted">
          Nothing saved. Unlike artifacts, state survives re-runs — an agent stores its cursor
          here to resume where it left off.
        </p>
      ) : (
        state.map((s) => (
          <StateEntry
            key={s.key}
            entry={s}
            busy={busy}
            open={expanded === s.key}
            onToggle={() => setExpanded((k) => (k === s.key ? null : s.key))}
            onRemove={() => orch.removeStateKey(s.key)}
          />
        ))
      )}
    </div>
  );
}

function WebhookRow({ trigger, busy, onRemove }: { trigger: AgentTrigger; busy: boolean; onRemove: () => void }) {
  const [copied, flash] = useFlash<true>(1500);
  const copy = () => {
    const url = `${window.location.origin}/api/agents/hooks/${trigger.token}`;
    void navigator.clipboard?.writeText(url).then(() => flash(true));
  };
  return (
    <div className="flex items-center gap-1.5 rounded border border-border bg-surface/50 px-2 py-1">
      <Webhook size={11} className="shrink-0 text-muted" />
      <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted">
        …/hooks/{trigger.token.slice(0, 8)}
      </span>
      <button
        type="button"
        onClick={copy}
        className="rounded p-0.5 text-muted hover:text-accent"
        data-tooltip={copied ? "Copied!" : "Copy URL"}
        aria-label="Copy webhook URL"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      <button
        type="button"
        onClick={onRemove}
        disabled={busy}
        className="rounded p-0.5 text-muted hover:text-red-500 disabled:opacity-50"
        aria-label="Delete webhook"
      >
        <Trash2 size={12} />
      </button>
    </div>
  );
}

// External webhook triggers.
function WebhookList({ orch }: { orch: AgentOrchestration }) {
  const add = (
    <button
      type="button"
      onClick={orch.addWebhook}
      disabled={orch.busy}
      className="rounded p-0.5 text-muted hover:bg-surface disabled:opacity-50"
      data-tooltip="Create a webhook trigger"
      aria-label="Create a webhook trigger"
    >
      <Plus size={14} />
    </button>
  );
  return (
    <div className="space-y-2">
      <SectionTitle icon={<Webhook size={14} />} title="Webhooks" action={add} />
      {orch.triggers.length === 0 ? (
        <p className="text-[11px] text-muted">No triggers. Add a webhook to re-run this agent from an external event.</p>
      ) : (
        orch.triggers.map((t) => (
          <WebhookRow key={t.id} trigger={t} busy={orch.busy} onRemove={() => orch.removeWebhook(t.id)} />
        ))
      )}
    </div>
  );
}

// Per-agent orchestration cockpit shown in the insights sidebar: the shared
// artifacts this agent published to the blackboard, its durable cross-run state
// and its external webhook triggers. Everything a running agent exposes lives
// here so the single-agent view doubles as an orchestration surface rather than
// an isolated transcript.
export function AgentOrchestrationSection({
  agentId,
  artifacts,
  artifactLabel,
  onOpenArtifact,
}: {
  agentId: number;
  /** Loaded by the view, which also renders them as the Result tab. */
  artifacts: AgentArtifact[];
  /** The result version an artifact belongs to ("v2"), if it's one. */
  artifactLabel: (a: AgentArtifact) => string | null;
  onOpenArtifact: (a: AgentArtifact) => void;
}) {
  const orch = useAgentOrchestration(agentId);
  return (
    <div className="space-y-4">
      <ArtifactsIndex artifacts={artifacts} artifactLabel={artifactLabel} onOpenArtifact={onOpenArtifact} />
      <StateList orch={orch} />
      <WebhookList orch={orch} />
    </div>
  );
}
