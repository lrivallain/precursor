/**
 * Suggested follow-up replies — client mirror of the backend `suggest` parser.
 *
 * The backend already strips the trailing ```suggest block from persisted
 * messages and ships the options as structured `suggestions`. These helpers are
 * used where that hasn't happened yet: hiding the block from a still-streaming
 * assistant bubble, and parsing it out of agent timeline text (which comes
 * straight from the SDK, unparsed).
 */

// Mirror of `_SUGGEST_BLOCK_RE` in precursor/backend/services/suggestions.py.
// Excluding ``` from the body keeps the match self-contained (only the final
// block is lifted); the `$` close branch tolerates an unterminated fence so a
// mid-stream block is hidden too.
const SUGGEST_BLOCK_RE = /\n*```suggest[^\n]*\n((?:(?!```)[\s\S])*?)(?:\n?```|$)\s*$/i;
const LIST_MARKER_RE = /^\s*(?:[-*+]|\d+[.)])\s+/;
const MAX_SUGGESTIONS = 5;

/** Parse the options out of a trailing `suggest` block (empty when none). */
export function parseSuggestions(text: string): string[] {
  if (!text) return [];
  const match = SUGGEST_BLOCK_RE.exec(text.trimEnd());
  if (!match) return [];
  const items: string[] = [];
  const seen = new Set<string>();
  for (const raw of match[1].split("\n")) {
    const line = raw.trim().replace(LIST_MARKER_RE, "").trim();
    if (!line || seen.has(line)) continue;
    seen.add(line);
    items.push(line);
    if (items.length >= MAX_SUGGESTIONS) break;
  }
  return items;
}

/** Remove a trailing `suggest` block so it never shows as raw markup. */
export function stripSuggestionBlock(text: string): string {
  if (!text || !/```suggest/i.test(text)) return text;
  return text.replace(SUGGEST_BLOCK_RE, "").trimEnd();
}
