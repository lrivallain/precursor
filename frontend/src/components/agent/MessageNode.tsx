import { Fragment, useRef, useState, type ReactNode, type RefObject } from "react";
import { Check, CheckCircle2, ChevronDown, Code2, Copy, CornerDownRight, HelpCircle, History, Pencil } from "lucide-react";
import { messageModel, type MessageModel } from "../../lib/agentNodeModel";
import type { NodeCategory } from "../../lib/agentWorkflow";
import type { AgentArtifactDirective } from "../../lib/directives";
import { HighlightedText } from "../../lib/searchHighlight";
import { useFlash } from "../../lib/useFlash";
import type { AgentEvent } from "../../lib/types";
import { InlineArtifact } from "../AgentResults";
import { CATEGORY_STYLE } from "../AgentTimeline";
import { CopyMessageLink } from "../CopyMessageLink";
import { Markdown } from "../Markdown";
import { MessageMeta } from "../MessageMeta";
import { ReasoningDisclosure } from "../ReasoningDisclosure";
import { SuggestedReplies } from "../SuggestedReplies";
import { clip, NodeAvatar, ThinkingNode, type Persona } from "./TimelineNodes";

export interface MessageNodeProps {
  event: AgentEvent;
  permalink?: string;
  category: NodeCategory;
  /** The round's thinking, shown collapsed above the message. */
  reasoning?: string | null;
  isLastAnswer: boolean;
  // Whether the parent agent is autonomy-enabled — gates directive parsing so a
  // normal agent that happens to type "PROGRESS:" isn't rewritten.
  autonomy?: boolean;
  user?: Persona;
  // Session model + computed generation time, shown on assistant answers only.
  model?: string | null;
  elapsedMs?: number | null;
  onPickSuggestion?: (text: string) => void;
  // Focus the reply composer — offered on a raised NEED_INPUT question.
  onReply?: () => void;
  suggestionsDisabled?: boolean;
  /**
   * How an `ARTIFACT:` this message published is shown in its place. The
   * cockpit links it to the Result tab; without a renderer (a workflow step's
   * trace) it folds inline, so the deliverable is never lost with the prose.
   */
  renderArtifact?: (artifact: AgentArtifactDirective, index: number) => ReactNode;
  /** The prompt of the turn the timeline rail marks as current. */
  highlighted?: boolean;
  /** Prompt toolbar rewind actions (see useAgentRewind). */
  onRewindHere?: () => void;
  onEditResend?: () => void;
  /** Show the prompt toolbar without hovering (the turn being read back). */
  revealActions?: boolean;
}

// A raised NEED_INPUT question, surfaced as a prominent amber callout inside the
// assistant bubble so the one line a human must act on never hides in prose.
function NeedInputCallout({ question, onReply }: { question: string; onReply?: () => void }) {
  return (
    <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-amber-800 dark:text-amber-200">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide">
        <HelpCircle size={13} className="shrink-0" />
        Needs your input
      </div>
      <Markdown className="mt-1 text-[12px] font-medium leading-relaxed text-text">{question}</Markdown>
      {onReply && (
        <button
          type="button"
          onClick={onReply}
          className="mt-2 inline-flex items-center gap-1 rounded-md bg-amber-500/20 px-2 py-1 text-[11px] font-medium text-amber-900 transition hover:bg-amber-500/30 dark:text-amber-100"
        >
          <CornerDownRight size={12} /> Answer
        </button>
      )}
    </div>
  );
}

function AnswerBadge({ isCompletion, isLastAnswer }: { isCompletion: boolean; isLastAnswer: boolean }) {
  if (isCompletion) {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 size={10} className="shrink-0" />
        Objective complete
      </span>
    );
  }
  if (!isLastAnswer) return null;
  return (
    <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
      Answer
    </span>
  );
}

// The system message (the long base prompt) is collapsed to a few lines with a
// Details toggle, like a tool box, so it never floods the timeline.
function SystemBody({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <p className={`whitespace-pre-wrap text-[11px] text-muted ${open ? "" : "line-clamp-3"}`}>{text}</p>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-1 flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] text-muted hover:bg-bg"
        data-tooltip={open ? "Collapse" : "Show full system message"}
      >
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
        {open ? "Hide" : "Details"}
      </button>
    </div>
  );
}

function AssistantBody({
  model,
  contentRef,
  renderArtifact,
  onReply,
}: {
  model: MessageModel;
  contentRef: RefObject<HTMLDivElement | null>;
  renderArtifact?: MessageNodeProps["renderArtifact"];
  onReply?: () => void;
}) {
  const { bodyText, directives } = model;
  return (
    <div ref={contentRef}>
      {bodyText && <Markdown className="mt-1 text-[11px] leading-relaxed text-muted">{bodyText}</Markdown>}
      {directives?.artifacts.map((a, i) => (
        <Fragment key={i}>{renderArtifact ? renderArtifact(a, i) : <InlineArtifact artifact={a} />}</Fragment>
      ))}
      {directives?.needInput && <NeedInputCallout question={directives.needInput} onReply={onReply} />}
    </div>
  );
}

const TOOLBAR =
  "hover-reveal absolute -bottom-3 z-10 flex items-center gap-1 rounded-full border border-border bg-surface px-1 py-0.5 shadow-sm transition-opacity group-hover/node:opacity-100 group-focus-within/node:opacity-100";

function CopyButton({ copied, label, icon, onClick }: { copied: boolean; label: string; icon: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full p-1 text-muted hover:text-accent"
      aria-label={label}
      data-tooltip={label}
    >
      {copied ? <Check size={12} className="text-emerald-500" /> : icon}
    </button>
  );
}

// Copy the rendered text (markdown stripped) or the raw markdown source, the
// same pair of actions offered on topic/chat assistant bubbles.
function AssistantToolbar({
  permalink,
  hasText,
  copyText,
}: {
  permalink?: string;
  hasText: boolean;
  copyText: (kind: "text" | "md") => Promise<string | null>;
}) {
  const [copied, flash] = useFlash<"text" | "md">(1200);
  const copy = async (kind: "text" | "md") => {
    const value = await copyText(kind);
    if (value == null) return;
    try {
      await navigator.clipboard.writeText(value);
      flash(kind);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  };
  return (
    <div className={`${TOOLBAR} right-2 opacity-0`}>
      {permalink && <CopyMessageLink path={permalink} />}
      {hasText && (
        <>
          <CopyButton copied={copied === "text"} label="Copy message" icon={<Copy size={12} />} onClick={() => void copy("text")} />
          <CopyButton copied={copied === "md"} label="Copy raw markdown" icon={<Code2 size={12} />} onClick={() => void copy("md")} />
        </>
      )}
    </div>
  );
}

function PromptToolbar({
  permalink,
  onRewindHere,
  onEditResend,
  reveal,
}: {
  permalink?: string;
  onRewindHere?: () => void;
  onEditResend?: () => void;
  reveal: boolean;
}) {
  return (
    <div className={`${TOOLBAR} left-2 ${reveal ? "opacity-100" : "opacity-0"}`}>
      {permalink && <CopyMessageLink path={permalink} />}
      {onRewindHere && (
        <button
          type="button"
          onClick={onRewindHere}
          className="rounded-full p-1 text-muted hover:text-amber-500"
          aria-label="Rewind here"
          data-tooltip={"Rewind here\nKeep this turn and drop the later ones"}
        >
          <History size={12} />
        </button>
      )}
      {onEditResend && (
        <button
          type="button"
          onClick={onEditResend}
          className="rounded-full p-1 text-muted hover:text-accent"
          aria-label="Edit and resend"
          data-tooltip={"Edit & resend\nDrop this turn and the later ones, and edit this prompt"}
        >
          <Pencil size={12} />
        </button>
      )}
    </div>
  );
}

function MessageBody({
  props,
  model,
  contentRef,
}: {
  props: MessageNodeProps;
  model: MessageModel;
  contentRef: RefObject<HTMLDivElement | null>;
}) {
  const text = props.event.text;
  if (!text) return null;
  if (props.category === "system") return <SystemBody text={text} />;
  if (props.category === "assistant") {
    return (
      <AssistantBody model={model} contentRef={contentRef} renderArtifact={props.renderArtifact} onReply={props.onReply} />
    );
  }
  return (
    <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">
      <HighlightedText text={clip(text)} />
    </p>
  );
}

// The card's colours: the newest answer is set apart in green.
function nodeColours(category: NodeCategory, isLastAnswer: boolean): { box: string; marker: string } {
  if (isLastAnswer) {
    return {
      box: "border-emerald-500/50 bg-emerald-500/15 ring-1 ring-emerald-500/30",
      marker: "border-emerald-500/50 bg-emerald-500/20 text-emerald-600 dark:text-emerald-300",
    };
  }
  return CATEGORY_STYLE[category];
}

function MessageHeader({ props, model }: { props: MessageNodeProps; model: MessageModel }) {
  const { category, isLastAnswer } = props;
  const style = CATEGORY_STYLE[category];
  // For the user's own prompt, show their GitHub persona rather than a generic icon/label.
  const persona = category === "user" ? props.user : undefined;
  return (
    <div className="flex items-center gap-2">
      <NodeAvatar user={persona} marker={nodeColours(category, isLastAnswer).marker} icon={style.icon} />
      <span className={`text-[11px] font-semibold ${persona ? "" : "capitalize"}`}>{persona ? persona.name : style.label}</span>
      <AnswerBadge isCompletion={model.isCompletion} isLastAnswer={isLastAnswer} />
    </div>
  );
}

function MessageToolbars({
  props,
  model,
  contentRef,
}: {
  props: MessageNodeProps;
  model: MessageModel;
  contentRef: RefObject<HTMLDivElement | null>;
}) {
  const { event, permalink, category } = props;
  if (category === "assistant") {
    if (!event.text && !permalink) return null;
    const copyText = async (kind: "text" | "md") =>
      kind === "md" ? model.bodyText : (contentRef.current?.textContent ?? event.text ?? "").trim();
    return <AssistantToolbar permalink={permalink} hasText={Boolean(event.text)} copyText={copyText} />;
  }
  if (category !== "user" || !(permalink || props.onRewindHere || props.onEditResend)) return null;
  return (
    <PromptToolbar
      permalink={permalink}
      onRewindHere={props.onRewindHere}
      onEditResend={props.onEditResend}
      reveal={Boolean(props.revealActions)}
    />
  );
}

/** One message node of the agent timeline (user/system/assistant/reasoning/error). */
export function MessageNode(props: MessageNodeProps) {
  const { event, category, isLastAnswer, reasoning } = props;
  const isAssistant = category === "assistant";
  const model = messageModel(event.text ?? "", {
    assistant: isAssistant,
    autonomy: Boolean(props.autonomy),
    offerSuggestions: isLastAnswer && Boolean(props.onPickSuggestion),
  });
  const contentRef = useRef<HTMLDivElement>(null);
  if (model.heartbeatOnly) return reasoning ? <ThinkingNode reasoning={reasoning} /> : null;
  return (
    <div
      className={`group/node relative w-full max-w-xl rounded-lg border p-2.5 transition hover:border-accent hover:ring-2 hover:ring-accent/40 ${nodeColours(category, isLastAnswer).box}`}
    >
      {props.highlighted && (
        // Ties the prompt to the timeline rail's blue (current) dash.
        <span aria-hidden className="pointer-events-none absolute -right-2.5 top-1.5 bottom-1.5 w-[3px] rounded-full bg-accent" />
      )}
      <MessageHeader props={props} model={model} />
      {reasoning && <ReasoningDisclosure reasoning={reasoning} className="mt-1.5" />}
      <MessageBody props={props} model={model} contentRef={contentRef} />
      <MessageToolbars props={props} model={model} contentRef={contentRef} />
      {model.suggestions.length > 0 && props.onPickSuggestion && (
        <SuggestedReplies
          items={model.suggestions}
          onPick={props.onPickSuggestion}
          disabled={props.suggestionsDisabled}
          className="mt-2"
        />
      )}
      <div className="mt-1.5">
        <MessageMeta
          createdAt={event.at}
          model={isAssistant ? props.model : null}
          elapsedMs={isAssistant ? props.elapsedMs : null}
          hoverGroup="node"
        />
      </div>
    </div>
  );
}
