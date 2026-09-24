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
// An `ARTIFACT:` header line, a block terminator, and the directive line that
// ends an unterminated block — exactly `_ARTIFACT_HEADER_RE`, `_ARTIFACT_END_RE`
// and `_NARRATION_SKIP_RE` in the backend's directives module, with no tolerance
// for decoration. What the page shows as a published result must be what the
// backend published: a looser match would cut a deliverable short at an
// ordinary `- **Progress:** …` bullet, or swallow prose that merely mentions an
// artifact. Per-line (no `m` flag): artifacts are walked line by line.
const ARTIFACT_HEADER_RE = /^\s*ARTIFACT\s*:\s*(.*)$/i;
const ARTIFACT_END_RE = /^\s*(?:END[_ ]ARTIFACT|\/ARTIFACT|ARTIFACT[_ ]END)\s*$/i;
const DIRECTIVE_HEAD_RE = /^\s*(?:PROGRESS|NEED[_ ]INPUT|OBJECTIVE[_ ]COMPLETE|ARTIFACT)\s*:/i;

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
  out.artifacts = walkArtifacts(text).artifacts;
  return out;
}

// Drop trailing directive lines a model glued onto an artifact body. Mirrors
// `_strip_trailing_directives` in the backend.
function stripTrailingDirectiveLines(content: string): string {
  const lines = content.split("\n");
  while (lines.length > 0) {
    const last = lines[lines.length - 1];
    if (last.trim() && !DIRECTIVE_HEAD_RE.test(last)) break;
    lines.pop();
  }
  return lines.join("\n").trim();
}

/**
 * Walk a message for its published artifacts, in both shapes the backend's
 * `_extract_artifacts` accepts: an inline `ARTIFACT: <title> | <body>` line, and
 * a block — `ARTIFACT: <title>` (no pipe), the body on the following lines, then
 * `END_ARTIFACT` (or the next directive, or the end of the message).
 *
 * Returns the artifacts plus the message with every artifact removed, so the
 * prose around a deliverable can be rendered without repeating it.
 */
function walkArtifacts(text: string): { artifacts: AgentArtifactDirective[]; rest: string } {
  const lines = text.split(/\r?\n/);
  const artifacts: AgentArtifactDirective[] = [];
  const kept: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const header = ARTIFACT_HEADER_RE.exec(lines[i]);
    if (!header) {
      // A stray terminator (its header already consumed, or never sent) is
      // plumbing too.
      if (!ARTIFACT_END_RE.test(lines[i])) kept.push(lines[i]);
      i += 1;
      continue;
    }
    const rest = header[1].trim();
    let title: string;
    let body: string;
    const collected: string[] = [];
    if (rest.includes("|")) {
      const cut = rest.indexOf("|");
      title = rest.slice(0, cut).trim();
      body = normalizeArtifactMarkdown(rest.slice(cut + 1).trim());
      i += 1;
    } else {
      title = rest;
      i += 1;
      while (i < lines.length) {
        if (ARTIFACT_END_RE.test(lines[i])) {
          i += 1;
          break;
        }
        if (DIRECTIVE_HEAD_RE.test(lines[i])) break;
        collected.push(lines[i]);
        i += 1;
      }
      body = collected.join("\n").trim();
    }
    body = stripTrailingDirectiveLines(body);
    if (title && body) artifacts.push({ title: title.slice(0, 200), content: body });
    // Nothing was published (a block with no title): its body is still what the
    // agent wrote, so it stays in the prose rather than vanishing.
    else kept.push(...collected);
  }
  return { artifacts, rest: kept.join("\n") };
}

/** True when the text carries any autonomy directive marker. */
export function hasAgentDirective(text: string | null | undefined): boolean {
  if (!text) return false;
  DIRECTIVE_LINE_RE.lastIndex = 0;
  return DIRECTIVE_LINE_RE.test(text);
}

/**
 * Remove directive markers so the rendered body reads as clean prose. The
 * markers are surfaced separately (progress bar, completion state, the
 * NEED_INPUT callout, and published results), so dropping them here avoids
 * duplicating them as raw text. An `ARTIFACT` block goes as a whole — header,
 * body and terminator — because its body is the deliverable, rendered once as a
 * result rather than a second time inside the message. Collapses the blank
 * lines the removal leaves behind.
 */
export function stripAgentDirectives(text: string): string {
  if (!text) return text;
  DIRECTIVE_LINE_RE.lastIndex = 0;
  return walkArtifacts(text)
    .rest.replace(DIRECTIVE_LINE_RE, "")
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