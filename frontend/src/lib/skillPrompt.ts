import type { ParsedCommand } from "./commands";
import { skillsStore } from "./skillsStore";

export interface ResolvedPrompt {
  /** What the transcript keeps: the text as typed. */
  content: string;
  /** What the model receives instead, when skills were expanded. */
  promptOverride?: string;
}

/**
 * Turn a draft into the message to persist plus the prompt to send the model:
 * a leading `/skill args` becomes the skill's instructions followed by the
 * args, and a `/skill-name` elsewhere in the text is inlined in place.
 */
export function resolveSkillPrompt(
  content: string,
  parseCommand: (text: string) => ParsedCommand | null,
): ResolvedPrompt {
  const cmd = content ? parseCommand(content) : null;
  const skill = cmd ? skillsStore.byName(cmd.name) : undefined;
  if (cmd && skill) {
    return {
      content,
      promptOverride: `${skill.instructions.trim()}\n\n---\n\n${skillsStore.expandReferences(cmd.argument)}`,
    };
  }
  const inlined = content ? skillsStore.expandReferences(content) : content;
  return {
    content: content || "(attachment attached)",
    promptOverride: inlined && inlined !== content ? inlined : undefined,
  };
}
