import { useRef, useState } from "react";
import {
  ArrowUpRight,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Code2,
  Copy,
  Paperclip,
  StopCircle,
  Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import { useSkills } from "../lib/skillsStore";
import type { Attachment, MessageRole, Skill } from "../lib/types";
import { Markdown } from "./Markdown";

interface Props {
  role: MessageRole;
  content: string;
  pending?: boolean;
  attachments?: Attachment[];
  onDelete?: () => void;
  // When provided on a pending assistant bubble, renders an inline Stop button
  // so the user can cancel the in-flight request from where they're looking.
  onStop?: () => void;
  // When true, renders the (repeated automation) prompt collapsed by default
  // with a toggle to reveal it, freeing room for the generated content.
  collapsible?: boolean;
  // Set when this turn was posted by an Agents-mode session — renders a badge
  // that deep-links back to /agents/{id}.
  agentSessionId?: number | null;
}

const roleLabel: Record<MessageRole, string> = {
  user: "You",
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
};

/**
 * If a user message starts with `/<skill-name>` and that skill exists,
 * return the matched skill + the remaining argument. Otherwise null.
 */
function matchSkillInvocation(
  content: string,
  skills: Skill[],
): { skill: Skill; argument: string } | null {
  const m = content.match(/^\/([a-z][a-z0-9-]*)(?:\s+([\s\S]*))?$/i);
  if (!m) return null;
  const skill = skills.find((s) => s.name === m[1].toLowerCase());
  if (!skill) return null;
  return { skill, argument: (m[2] ?? "").trim() };
}

export function MessageBubble({ role, content, pending, attachments, onDelete, onStop, collapsible, agentSessionId }: Props) {
  const isUser = role === "user";
  const skills = useSkills();
  const skillInvocation =
    isUser && !pending ? matchSkillInvocation(content, skills) : null;
  const showThinking = pending && !content;
  const imageAttachments = (attachments ?? []).filter((a) => a.mime.startsWith("image/"));
  const fileAttachments = (attachments ?? []).filter((a) => !a.mime.startsWith("image/"));
  const [hover, setHover] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<null | "text" | "md">(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // Copy the rendered text (markdown stripped) or the raw markdown source.
  // Output (assistant) bubbles expose both alongside delete.
  const copyTo = async (kind: "text" | "md") => {
    const value =
      kind === "md"
        ? content
        : (contentRef.current?.textContent ?? content).trim();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  };
  const showActions = !pending && !!content && (role === "assistant" || onDelete);

  // Persisted SYSTEM rows are UI-only notices (e.g. the "Run now accepted"
  // confirmation). Render them as a compact green, user-aligned acknowledgement.
  if (role === "system") {
    return (
      <div className="flex flex-col items-end gap-1">
        <div className="inline-flex items-center gap-1.5 max-w-full rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />
          <span>{content}</span>
        </div>
      </div>
    );
  }

  // Collapsible automation prompt: a repeated scheduled-run prompt. Collapsed by
  // default to a one-line summary so generated content gets the room.
  if (collapsible && !pending) {
    const firstLine = content.split("\n", 1)[0];
    const preview =
      firstLine.length > 80 ? `${firstLine.slice(0, 80)}…` : firstLine;
    return (
      <div
        className="flex flex-col items-end gap-1 group"
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      >
        <div className="text-[11px] uppercase tracking-wide text-muted">
          {roleLabel[role]}
        </div>
        <div className="relative rounded-lg border border-border bg-accent/10 max-w-full">
          {onDelete && (
            <button
              type="button"
              onClick={onDelete}
              style={{ opacity: hover ? 1 : 0, transition: "opacity 120ms ease-out" }}
              className="absolute -top-2 -left-2 p-1 rounded-full bg-surface border border-border text-muted hover:text-red-500"
              aria-label="Delete message"
              data-tooltip="Delete message"
            >
              <Trash2 size={12} />
            </button>
          )}
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="flex items-center gap-1.5 w-full px-3 py-1.5 text-left text-sm text-muted hover:text-text"
            aria-expanded={expanded}
          >
            {expanded ? (
              <ChevronDown size={14} className="shrink-0" />
            ) : (
              <ChevronRight size={14} className="shrink-0" />
            )}
            <span className="text-[11px] uppercase tracking-wide shrink-0">
              Prompt
            </span>
            {!expanded && (
              <span className="truncate text-text/70 font-normal normal-case">
                {preview}
              </span>
            )}
          </button>
          {expanded && (
            <div className="px-3 pb-2 -mt-0.5">
              <Markdown className="text-sm leading-relaxed">
                {content || "\u200B"}
              </Markdown>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="flex items-center gap-2">
        <div className="text-[11px] uppercase tracking-wide text-muted">{roleLabel[role]}</div>
        {agentSessionId != null && <AgentExchangeBadge agentSessionId={agentSessionId} />}
      </div>
      <div
        className={`relative px-3 py-2 rounded-lg border border-border ${
          isUser ? "bg-accent/10" : "bg-surface"
        } max-w-full`}
      >
        {showActions && (
          <div
            style={{
              opacity: hover ? 1 : 0,
              transition: "opacity 120ms ease-out",
            }}
            className={`absolute -bottom-3 ${
              isUser ? "left-2" : "right-2"
            } z-10 flex items-center gap-1 rounded-full bg-surface border border-border px-1 py-0.5 shadow-sm`}
          >
            {role === "assistant" && (
              <>
                <button
                  type="button"
                  onClick={() => copyTo("text")}
                  className="p-1 rounded-full text-muted hover:text-accent"
                  aria-label="Copy message"
                  data-tooltip="Copy message"
                >
                  {copied === "text" ? (
                    <Check size={12} className="text-emerald-500" />
                  ) : (
                    <Copy size={12} />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => copyTo("md")}
                  className="p-1 rounded-full text-muted hover:text-accent"
                  aria-label="Copy raw markdown"
                  data-tooltip="Copy raw markdown"
                >
                  {copied === "md" ? (
                    <Check size={12} className="text-emerald-500" />
                  ) : (
                    <Code2 size={12} />
                  )}
                </button>
              </>
            )}
            {onDelete && (
              <button
                type="button"
                onClick={onDelete}
                className="p-1 rounded-full text-muted hover:text-red-500"
                aria-label="Delete message"
                data-tooltip="Delete message"
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
        )}
        {showThinking ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <ThinkingDots />
            <span className="italic">Thinking…</span>
            {onStop && (
              <button
                type="button"
                onClick={onStop}
                className="ml-1 inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted hover:text-red-500 hover:border-red-500/50"
                aria-label="Stop generation"
                data-tooltip="Stop generation"
              >
                <StopCircle size={12} />
                Stop
              </button>
            )}
          </div>
        ) : skillInvocation ? (
          <SkillInvocation
            skill={skillInvocation.skill}
            argument={skillInvocation.argument}
          />
        ) : (
          <>
            {imageAttachments.length > 0 && (
              <div className="mb-1.5 flex flex-wrap gap-1.5">
                {imageAttachments.map((a) => (
                  <a
                    key={a.id}
                    href={api.attachmentUrl(a.id)}
                    target="_blank"
                    rel="noreferrer"
                    title={a.original_filename || `image-${a.id}`}
                    className="block"
                  >
                    <img
                      src={api.attachmentUrl(a.id)}
                      alt={a.original_filename || ""}
                      className="max-w-[18rem] max-h-64 rounded border border-border object-contain bg-bg"
                    />
                  </a>
                ))}
              </div>
            )}
            {fileAttachments.length > 0 && (
              <div className="mb-1.5 flex flex-wrap gap-1.5">
                {fileAttachments.map((a) => (
                  <a
                    key={a.id}
                    href={api.attachmentUrl(a.id)}
                    target="_blank"
                    rel="noreferrer"
                    title={a.original_filename || `attachment-${a.id}`}
                    className="inline-flex items-center gap-1.5 rounded border border-border bg-bg px-2 py-1 text-xs hover:bg-surface"
                  >
                    <Paperclip size={12} />
                    <span className="max-w-[16rem] truncate">
                      {a.original_filename || `attachment-${a.id}`}
                    </span>
                  </a>
                ))}
              </div>
            )}
            <div ref={contentRef}>
              <Markdown className="text-sm leading-relaxed">
                {content || "\u200B"}
              </Markdown>
            </div>
            {pending && (
              <div className="mt-1 flex items-center gap-2 text-[11px] text-muted">
                <span className="italic">streaming…</span>
                {onStop && (
                  <button
                    type="button"
                    onClick={onStop}
                    className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:text-red-500 hover:border-red-500/50"
                    aria-label="Stop generation"
                    data-tooltip="Stop generation"
                  >
                    <StopCircle size={12} />
                    Stop
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export function AgentExchangeBadge({ agentSessionId }: { agentSessionId: number }) {
  return (
    <button
      type="button"
      onClick={() => {
        window.dispatchEvent(
          new CustomEvent("precursor:open-agent", { detail: { id: agentSessionId } }),
        );
      }}
      className="group inline-flex cursor-pointer items-center gap-1 rounded-full border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-violet-600 hover:bg-violet-500/20 dark:text-violet-300"
      title="Open the agent session"
      data-tooltip="Open the agent session"
    >
      <Bot size={11} />
      Agent
      <ArrowUpRight size={11} className="opacity-60 transition group-hover:opacity-100" />
    </button>
  );
}

function SkillInvocation({ skill, argument }: { skill: Skill; argument: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="text-sm leading-relaxed space-y-1">
      <div className="flex items-baseline gap-1 flex-wrap">
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          title={expanded ? "Hide skill instructions" : "Show skill instructions"}
          className="inline-flex items-center gap-0.5 font-mono text-accent hover:underline cursor-pointer"
        >
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          /{skill.name}
        </button>
        {argument && <span className="whitespace-pre-wrap break-words">{argument}</span>}
      </div>
      {expanded && (
        <div className="mt-1 border border-border rounded bg-bg/60 p-2 text-xs space-y-1">
          {skill.description && (
            <div className="text-muted italic">{skill.description}</div>
          )}
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-text/90">
            {skill.instructions}
          </pre>
        </div>
      )}
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="inline-flex gap-1" aria-label="Thinking">
      <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:-0.3s]" />
      <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:-0.15s]" />
      <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce" />
    </span>
  );
}
