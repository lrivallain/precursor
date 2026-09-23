import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import {
  matchSlashCommands,
  parseSlashCommand,
  surfaceExcludes,
  type CommandSurface,
  type ParsedCommand,
  type SlashCommand,
} from "./commands";
import { useSettings } from "./settingsStore";
import { useSkills } from "./skillsStore";
import { useAzureSpeech } from "./useAzureSpeech";

export interface UseComposerInputOptions {
  /** Which surface's built-in commands the picker and parser accept. */
  surface: CommandSurface;
  /** Extra built-ins to hide on top of the surface's own (e.g. feature flags). */
  exclude?: ReadonlySet<string>;
}

export interface ComposerInput {
  draft: string;
  setDraft: Dispatch<SetStateAction<string>>;
  /** Transient dictation text, shown until Azure finalises the chunk. */
  interimText: string;
  speech: ReturnType<typeof useAzureSpeech>;
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
  const settings = useSettings();

  // Live speech-to-text via Azure (when configured server-side). Final chunks
  // are appended to the draft as the user speaks; the interim transcript is
  // shown transiently. The mic is hidden entirely when Azure isn't configured.
  const [interimText, setInterimText] = useState("");
  const speech = useAzureSpeech({
    onFinalChunk: (text) => {
      const chunk = text.trim();
      if (!chunk) return;
      setDraft((d) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk));
      setInterimText("");
    },
    onInterim: setInterimText,
    enabled: settings?.stt_azure_ready ?? false,
    lang: settings?.azure_speech_language || undefined,
  });
  // Drop any lingering interim text once dictation stops.
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

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
