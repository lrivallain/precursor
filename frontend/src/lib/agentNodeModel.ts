// What the agent timeline's message and tool nodes show, derived from their
// event or grouped step (rendering lives in components/agent/).
import type { ToolStep } from "./agentWorkflow";
import { parseAgentDirectives, stripAgentDirectives, type AgentDirectives } from "./directives";
import { parseSuggestions, stripSuggestionBlock } from "./suggestions";

export interface MessageModel {
  /** Control directives of an autonomy agent's assistant turn. */
  directives: AgentDirectives | null;
  /** The prose to render: directives and the `suggest` block stripped. */
  bodyText: string;
  /** A terminal OBJECTIVE_COMPLETE turn: the completion *is* the answer. */
  isCompletion: boolean;
  /** Suggested replies offered under the newest answer. */
  suggestions: string[];
  /**
   * A heartbeat-only message (`PROGRESS:` and nothing else). It's drawn as its
   * milestone on the spine; an empty bubble above it would only add noise.
   */
  heartbeatOnly: boolean;
}

export function messageModel(
  text: string,
  {
    assistant,
    autonomy,
    offerSuggestions,
  }: {
    assistant: boolean;
    /**
     * Gates directive parsing so a normal agent that happens to type
     * "PROGRESS:" isn't rewritten.
     */
    autonomy: boolean;
    /** The newest answer, with a handler for the chips. */
    offerSuggestions: boolean;
  },
): MessageModel {
  if (!assistant) {
    return { directives: null, bodyText: text, isCompletion: false, suggestions: [], heartbeatOnly: false };
  }
  // Assistant turns may end with a `suggest` block; hide it from the rendered
  // text and surface it as chips under the final answer instead.
  const prose = stripSuggestionBlock(text);
  const suggestions = offerSuggestions ? parseSuggestions(text) : [];
  if (!autonomy) {
    return { directives: null, bodyText: prose, isCompletion: false, suggestions, heartbeatOnly: false };
  }
  // Autonomy agents embed control directives (PROGRESS/ARTIFACT/…): strip those
  // lines so the body reads as prose and a raised NEED_INPUT question stands
  // out as a callout. A completion folds its summary in as the body when the
  // prose was entirely directives.
  const directives = parseAgentDirectives(text);
  const completion = directives.complete ?? null;
  const bodyText = stripAgentDirectives(prose) || (completion ?? "");
  return {
    directives,
    bodyText,
    isCompletion: completion != null,
    suggestions,
    heartbeatOnly: !bodyText && !directives.needInput && directives.artifacts.length === 0,
  };
}

export interface ToolStatus {
  label: string;
  /** Still running (spinner). */
  running: boolean;
  /** Unterminated in an attempt that has ended. */
  stale: boolean;
  /** Drawn in the permission colours: awaiting approval, or stale. */
  alert: boolean;
}

export function toolStatus(step: ToolStep, closed: boolean): ToolStatus {
  const stale = closed && !step.done && !step.pending;
  let label = "running";
  if (step.pending) label = "awaiting approval";
  else if (step.done) label = "done";
  // A closed attempt that stopped at a gate never ran the call at all; one that
  // stopped elsewhere was cut off mid-flight.
  else if (closed) label = step.gated ? "never approved — attempt ended" : "interrupted — attempt ended";
  return { label, running: label === "running", stale, alert: Boolean(step.pending) || stale };
}
