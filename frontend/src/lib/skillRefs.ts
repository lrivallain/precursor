/**
 * Inline `/skill-name` references.
 *
 * A leading slash command is handled by the composers (see `commands.ts`), which
 * keep the classic "instructions + `---` + argument" shape and persist the
 * literal command. *Anywhere else* in a message a `/skill-name` token is
 * substituted in place with the skill's instructions, so prose like
 * "tidy this with /rewrite please" reads as the instructions embedded where the
 * user wrote them.
 *
 * Mirrors `precursor/backend/services/skills.py` (`expand_references`), which
 * applies the same rules to a chat description used as a system prompt. Keep the
 * two in sync.
 */

/**
 * A `/skill-name` reference. Anchored to the start of the string or to
 * whitespace, and refusing a trailing word char or `/`, so paths (`/usr/bin`),
 * URLs (`https://host/rewrite`) and prose (`and/or`) are never mistaken for a
 * skill call.
 *
 * Built per call: a shared `/g` regex carries `lastIndex` state, which nested
 * expansion would re-enter.
 */
const skillRefPattern = (): RegExp => /(^|\s)\/([a-z][a-z0-9-]*)(?![\w/-])/g;

/**
 * How many times a skill's own body may itself expand a reference. Cycles are
 * additionally blocked by tracking the names on the current expansion path.
 */
export const MAX_REFERENCE_DEPTH = 2;

/**
 * Substitute every `/skill-name` reference in `text` with its instructions.
 * Unknown or inactive names are left untouched — far more likely to be ordinary
 * prose than a typo'd skill call.
 */
export function expandSkillReferences(
  text: string,
  instructionsByName: ReadonlyMap<string, string>,
  maxDepth: number = MAX_REFERENCE_DEPTH,
): string {
  if (!text || !text.includes("/") || instructionsByName.size === 0) return text;

  const expand = (chunk: string, depth: number, path: ReadonlySet<string>): string =>
    chunk.replace(skillRefPattern(), (match, lead: string, name: string) => {
      const body = instructionsByName.get(name);
      // A name already on this expansion path would recurse forever.
      if (body === undefined || path.has(name)) return match;
      const trimmed = body.trim();
      if (depth <= 0) return `${lead}${trimmed}`;
      return `${lead}${expand(trimmed, depth - 1, new Set([...path, name]))}`;
    });

  return expand(text, maxDepth, new Set());
}
