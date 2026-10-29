/**
 * Question detection for the Live "suggested answers" feature.
 *
 * The transcriber hands us final segments; a question is often split across
 * several of them and the speaker may keep going, so we coalesce consecutive
 * segments from one speaker and decide once they settle. Detection is a cheap
 * client-side heuristic (punctuation + interrogative openers, EN/FR) so a
 * suggestion can start without a model round trip; the server prompt can still
 * answer `[[NONE]]` for false positives.
 */

const INTERROGATIVES = new RegExp(
  "^(?:" +
    [
      // English
      "what|what's|whats|why|how|when|where|who|whom|whose|which",
      "can|could|would|should|will|shall|may|might|do|does|did|is|are|am|was|were|have|has|had|isn't|aren't|don't|doesn't|didn't|won't|wouldn't|couldn't|shouldn't",
      // French
      "qu'est-ce|qu'est|quel|quelle|quels|quelles|quand|comment|pourquoi|combien|où|qui|que|quoi|lequel|laquelle",
      "est-ce|peut-on|peux-tu|pouvez-vous|pourrait|pourriez|pourrais|as-tu|avez-vous|a-t-on|y a-t-il|faut-il|doit-on|on peut|tu peux|vous pouvez|c'est quoi",
    ].join("|") +
    ")\\b",
  "i",
);

export const MIN_QUESTION_WORDS = 4;
export const MAX_QUESTION_CHARS = 600;

function sentences(text: string): string[] {
  return text
    .split(/(?<=[.!?…])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Whether the trailing utterance reads as a question that expects an answer. */
export function looksLikeQuestion(text: string): boolean {
  const all = sentences(text.trim());
  const last = all[all.length - 1];
  if (!last) return false;
  const words = last.split(/\s+/).filter(Boolean);
  if (words.length < MIN_QUESTION_WORDS) return false;
  if (/\?["')\]]*$/.test(last)) return true;
  // Speech-to-text sometimes drops the "?" — fall back on an interrogative
  // opener, but only when the sentence is short enough to be one question.
  return INTERROGATIVES.test(last) && words.length <= 30;
}

/** The question to send: the trailing sentences, capped so the prompt stays small. */
export function questionText(text: string): string {
  const t = text.trim();
  if (t.length <= MAX_QUESTION_CHARS) return t;
  return t.slice(t.length - MAX_QUESTION_CHARS).replace(/^\S*\s/, "");
}

export interface QuestionGateOptions {
  onQuestion: (question: string, speaker: string | null) => void;
  /** The user (a "me" label) spoke: any in-flight or pending suggestion is moot. */
  onOwnSpeech: () => void;
  settleMs?: number;
  cooldownMs?: number;
  maxPerMinute?: number;
  now?: () => number;
}

export interface QuestionGate {
  push: (text: string, speaker: string | null, isMe: boolean) => void;
  dispose: () => void;
}

function normalise(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

/**
 * Coalesce segments per speaker, wait for them to settle, then fire at most one
 * `onQuestion` per cooldown and `maxPerMinute`, never twice for the same text.
 */
export function createQuestionGate({
  onQuestion,
  onOwnSpeech,
  settleMs = 800,
  cooldownMs = 4000,
  maxPerMinute = 6,
  now = () => Date.now(),
}: QuestionGateOptions): QuestionGate {
  let pending: { speaker: string | null; text: string } | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let lastNormalised = "";
  let lastFiredAt = -Infinity;
  const fired: number[] = [];

  function clearTimer(): void {
    if (timer) clearTimeout(timer);
    timer = null;
  }

  function flush(): void {
    clearTimer();
    const p = pending;
    pending = null;
    if (!p || !looksLikeQuestion(p.text)) return;
    const question = questionText(p.text);
    const key = normalise(question);
    const t = now();
    while (fired.length && t - fired[0] > 60_000) fired.shift();
    if (key === lastNormalised) return;
    if (t - lastFiredAt < cooldownMs || fired.length >= maxPerMinute) return;
    lastNormalised = key;
    lastFiredAt = t;
    fired.push(t);
    onQuestion(question, p.speaker);
  }

  return {
    push(text, speaker, isMe) {
      const clean = text.trim();
      if (!clean) return;
      if (isMe) {
        clearTimer();
        pending = null;
        onOwnSpeech();
        return;
      }
      if (pending && pending.speaker !== speaker) flush();
      pending = pending
        ? { speaker, text: `${pending.text} ${clean}` }
        : { speaker, text: clean };
      clearTimer();
      timer = setTimeout(flush, settleMs);
    },
    dispose() {
      clearTimer();
      pending = null;
    },
  };
}
