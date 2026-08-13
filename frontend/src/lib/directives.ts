/**
 * Autonomy control directives — client mirror of `parse_agent_directives` in
 * precursor/backend/services/agents/manager.py.
 *
 * Autonomous agents embed these one-per-line markers in their messages so the
 * backend can drive their lifecycle (report progress, raise a blocking
 * question, publish an artifact, declare completion). The raw text is persisted
 * verbatim, so without help the markers render as noisy plain text in the
 * transcript — burying the one line a human actually needs to act on
 * (`NEED_INPUT`). These helpers let the UI strip the markers from the rendered
 * body and surface the raised question as a prominent callout instead.
 *
 * Only applied to autonomy-enabled agents, matching the backend which parses
 * these solely when `autonomy_enabled` (so a normal agent that happens to type
 * one of these words is unaffected).
 */

// Leading markdown/quote decoration a model might prepend to a directive line
// (blockquote `>`, list markers, bold/italic `*`/`_`, inline code) — tolerated
// so a decorated marker is still recognised and stripped.
const LEAD = "[ \\t>*_`-]*";
// Emphasis right after the colon — the closing `**` of a bolded `**LABEL:**`
// label — eaten so it never leaks into (and unbalances the Markdown of) the
// captured value.
const POST = "[ \\t*_`]*";

// Anchored to line start (`^` + `m`) so a directive quoted or explained mid-
// sentence in prose (e.g. "…emit **NEED_INPUT:** to your dashboard…") does not
// misfire and surface a phantom blocking question.
const NEED_INPUT_RE = new RegExp(`^${LEAD}NEED[_ ]INPUT\\s*:${POST}(.+)`, "im");
const COMPLETE_RE = new RegExp(`^${LEAD}OBJECTIVE[_ ]COMPLETE\\s*:${POST}(.+)`, "im");
const PROGRESS_RE = new RegExp(`^${LEAD}PROGRESS\\s*:\\s*(\\d{1,3})\\s*(?:\\|\\s*(.+))?`, "im");
// Every `ARTIFACT: <title> | <content>` line (repeatable per message). Global +
// multiline so we can walk all of them; mirrors the backend's per-line capture.
const ARTIFACT_RE = new RegExp(`^${LEAD}ARTIFACT\\s*:\\s*([^|\\n]+?)\\s*\\|\\s*(.+)$`, "gim");

// A whole line that is *only* a directive marker — used to delete it from the
// rendered body. Anchored to line bounds with the multiline flag.
const DIRECTIVE_LINE_RE = new RegExp(
  `^${LEAD}(?:NEED[_ ]INPUT|OBJECTIVE[_ ]COMPLETE|PROGRESS|ARTIFACT)\\s*:.*$`,
  "gim",
);

export interface AgentArtifactDirective {
  /** Short name of the published output. */
  title: string;
  /** The output payload (may be markdown). */
  content: string;
}

export interface AgentDirectives {
  /** The question the agent raised (`NEED_INPUT:`), or null. */
  needInput: string | null;
  /** One-line completion summary (`OBJECTIVE_COMPLETE:`), or null. */
  complete: string | null;
  /** Self-reported progress (`PROGRESS: <0-100> | <label>`), or null. */
  progress: { value: number; label: string | null } | null;
  /** Named outputs published to the blackboard (`ARTIFACT: <title> | <content>`). */
  artifacts: AgentArtifactDirective[];
}

/** Parse the autonomy directives embedded in an assistant message. */
export function parseAgentDirectives(text: string | null | undefined): AgentDirectives {
  const out: AgentDirectives = {
    needInput: null,
    complete: null,
    progress: null,
    artifacts: [],
  };
  if (!text) return out;
  const need = NEED_INPUT_RE.exec(text);
  if (need) out.needInput = need[1].trim();
  const done = COMPLETE_RE.exec(text);
  if (done) out.complete = done[1].trim();
  const prog = PROGRESS_RE.exec(text);
  if (prog) {
    const value = Math.max(0, Math.min(100, Number.parseInt(prog[1], 10)));
    out.progress = { value, label: (prog[2] ?? "").trim() || null };
  }
  ARTIFACT_RE.lastIndex = 0;
  for (let m = ARTIFACT_RE.exec(text); m; m = ARTIFACT_RE.exec(text)) {
    const title = m[1].trim();
    const content = m[2].trim();
    if (title && content) out.artifacts.push({ title, content });
  }
  return out;
}

/** True when the text carries any autonomy directive marker. */
export function hasAgentDirective(text: string | null | undefined): boolean {
  if (!text) return false;
  DIRECTIVE_LINE_RE.lastIndex = 0;
  return DIRECTIVE_LINE_RE.test(text);
}

/**
 * Remove directive marker lines so the rendered body reads as clean prose. The
 * markers are surfaced separately (progress bar, completion state, and the
 * NEED_INPUT callout), so dropping them here avoids duplicating them as raw
 * text. Collapses the blank lines the removal leaves behind.
 */
export function stripAgentDirectives(text: string): string {
  if (!text) return text;
  DIRECTIVE_LINE_RE.lastIndex = 0;
  return text
    .replace(DIRECTIVE_LINE_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// Break a numbered list packed onto one physical line into separate lines. Only
// a strictly sequential `1, 2, 3, …` run is split, so incidental "2." tokens
// (decimals, versions, prices — which have no space after the dot) are left
// alone. Mirrors `_split_inline_ordered_list` in the backend manager.
function splitInlineOrderedList(text: string): string {
  const re = /(?:^|\s)(\d{1,2})\.\s/g;
  const starts: number[] = [];
  const nums: number[] = [];
  for (let m = re.exec(text); m; m = re.exec(text)) {
    // Anchor the split at the digit, not the leading whitespace the regex ate.
    const lead = m[0].length - m[0].replace(/^\s+/, "").length;
    starts.push(m.index + lead);
    nums.push(Number(m[1]));
  }
  const sequential = nums.length >= 2 && nums.every((n, i) => n === i + 1);
  if (!sequential) return text;
  const pieces: string[] = [];
  let prev = 0;
  for (let i = 1; i < starts.length; i += 1) {
    pieces.push(text.slice(prev, starts[i]).trimEnd());
    prev = starts[i];
  }
  pieces.push(text.slice(prev));
  return pieces.filter((p) => p).join("\n").trim();
}

/**
 * Coax a published artifact's payload into well-formed Markdown for rendering.
 *
 * A single `ARTIFACT:` directive is one physical line, so a model can't press
 * Enter inside it — multi-line deliverables (lists, paragraphs) would collapse
 * into one paragraph. The backend normalizes newly-published content, but this
 * mirror lets already-persisted artifacts render correctly at read time: it
 * unescapes a literal `\n`/`\t` the model may have used and, as a safety net,
 * breaks a packed sequential inline numbered list onto its own lines. Mirrors
 * `_normalize_artifact_content` in precursor/backend/services/agents/manager.py.
 */
export function normalizeArtifactMarkdown(content: string): string {
  let out = content;
  if (out.includes("\\n") || out.includes("\\t") || out.includes("\\r")) {
    out = out
      .replace(/\\r\\n/g, "\n")
      .replace(/\\r/g, "\n")
      .replace(/\\n/g, "\n")
      .replace(/\\t/g, "\t");
  }
  if (!out.includes("\n")) out = splitInlineOrderedList(out);
  return out;
}