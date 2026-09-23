import { useMemo, useState, type Dispatch, type SetStateAction } from "react";
import {
  matchSlashCommands,
  parseSlashCommand,
  surfaceExcludes,
  type CommandSurface,
  type ParsedCommand,
  type SlashCommand,
} from "./commands";
import { useSkills } from "./skillsStore";
import { useDictation, type Dictation } from "./useDictation";

export interface UseComposerInputOptions {
  /** Which surface's built-in commands the picker and parser accept. */
  surface: CommandSurface;
  /** Extra built-ins to hide on top of the surface's own (e.g. feature flags). */
  exclude?: ReadonlySet<string>;
}

export interface ComposerInput extends Dictation {
  draft: string;
  setDraft: Dispatch<SetStateAction<string>>;
  /** Active skills, shaped as slash commands. */
  skillCommands: SlashCommand[];
  /** Built-ins this composer never offers or parses. */
  excludedCommands: ReadonlySet<string>;
  /** Autocomplete rows for the command currently being typed. */
  suggestions: SlashCommand[];
  /** Recognise a leading built-in or skill command in `content`. */
  parseCommand: (content: string) => ParsedCommand | null;
}

/**
 * Draft, dictation and slash-command state shared by every chat composer. The
 * surface decides which built-ins are offered; skills are always included.
 */
export function useComposerInput({ surface, exclude }: UseComposerInputOptions): ComposerInput {
  const [draft, setDraft] = useState("");
  const { interimText, speech } = useDictation(setDraft);

  const skills = useSkills();
  const skillCommands = useMemo<SlashCommand[]>(
    () =>
      skills
        .filter((s) => s.active)
        .map((s) => ({
          name: s.name,
          label: `/${s.name}`,
          description: s.description ?? "",
          kind: "skill" as const,
          argumentHint: "input",
        })),
    [skills],
  );

  const excludedCommands = useMemo<ReadonlySet<string>>(() => {
    const set = new Set(surfaceExcludes(surface));
    for (const name of exclude ?? []) set.add(name);
    return set;
  }, [surface, exclude]);

  const suggestions = useMemo<SlashCommand[]>(
    () => matchSlashCommands(draft, skillCommands, excludedCommands) ?? [],
    [draft, skillCommands, excludedCommands],
  );

  const parseCommand = (content: string): ParsedCommand | null =>
    parseSlashCommand(content, skillCommands, excludedCommands);

  return {
    draft,
    setDraft,
    interimText,
    speech,
    skillCommands,
    excludedCommands,
    suggestions,
    parseCommand,
  };
}
