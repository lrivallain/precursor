/**
 * Slash-command registry for the chat composer.
 *
 * Built-in commands are listed here; user-defined skills are appended at
 * runtime via the `extra` argument passed by ChatPanel.
 */

export interface SlashCommand {
  name: string;
  label: string;
  description: string;
  /** Free-form hint for what comes after the command name. */
  argumentHint?: string;
  /** Marks commands that come from user-defined skills (vs. built-ins). */
  kind?: "builtin" | "skill";
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "gh-update",
    label: "/gh-update",
    description:
      "Draft a status comment on the linked GitHub issue. The text after the command is an instruction (e.g. 'ask the owner for an ETA').",
    argumentHint: "instruction (optional)",
    kind: "builtin",
  },
  {
    name: "gh-sync",
    label: "/gh-sync",
    description:
      "Force-refresh the linked GitHub issue. Invalidates the cached context so the Context tab regenerates next time.",
    kind: "builtin",
  },
  {
    name: "gh-create",
    label: "/gh-create",
    description:
      "Create a new GitHub issue from this conversation and link it to the topic. Editable title + body before posting.",
    argumentHint: "instruction (optional)",
    kind: "builtin",
  },
  {
    name: "gh-close",
    label: "/gh-close",
    description:
      "Close the linked GitHub issue, optionally with a closing comment. Editable before posting.",
    argumentHint: "instruction (optional)",
    kind: "builtin",
  },
  {
    name: "notes",
    label: "/notes",
    description:
      "Open a scratch pad to capture freeform notes (e.g. during a meeting). Then choose: rephrase via AI, post as a GitHub comment, or add to the chat with or without an AI follow-up.",
    kind: "builtin",
  },
  {
    name: "rename",
    label: "/rename",
    description:
      "Rename this topic. The text after the command becomes the new title.",
    argumentHint: "new title",
    kind: "builtin",
  },
  {
    name: "new",
    label: "/new",
    description:
      "Create a new topic nested under this one and switch to it. The text after the command is its title.",
    argumentHint: "title",
    kind: "builtin",
  },
  {
    name: "pin",
    label: "/pin",
    description: "Pin this topic to the top of the sidebar.",
    kind: "builtin",
  },
  {
    name: "unpin",
    label: "/unpin",
    description: "Remove this topic from the pinned list.",
    kind: "builtin",
  },
  {
    name: "clear",
    label: "/clear",
    description:
      "Erase the entire chat transcript for this topic (asks for confirmation). The topic and its GitHub link are kept.",
    kind: "builtin",
  },
  {
    name: "archive",
    label: "/archive",
    description:
      "Archive this topic and leave it. Restore it any time from the archive (your profile menu in the sidebar).",
    kind: "builtin",
  },
];

export interface ParsedCommand {
  name: string;
  argument: string;
}

/**
 * Recognise a slash command at the start of the input. Returns null when the
 * text is a normal message or when the command isn't known.
 */
export function parseSlashCommand(
  input: string,
  extra: SlashCommand[] = [],
  excludeNames: ReadonlySet<string> = new Set(),
): ParsedCommand | null {
  const trimmed = input.trimStart();
  if (!trimmed.startsWith("/")) return null;
  const match = trimmed.match(/^\/([a-z][a-z0-9-]*)\s*([\s\S]*)$/i);
  if (!match) return null;
  const name = match[1].toLowerCase();
  if (excludeNames.has(name)) return null;
  if (![...SLASH_COMMANDS, ...extra].some((c) => c.name === name)) return null;
  return { name, argument: match[2].trim() };
}

/**
 * Return commands matching the first token of the input. Used to drive the
 * autocomplete picker shown while the user is still typing the command name.
 *
 * Returns `null` when the input isn't a command-in-progress (no leading `/`,
 * or already past the command name with whitespace + an argument).
 */
export function matchSlashCommands(
  input: string,
  extra: SlashCommand[] = [],
  excludeNames: ReadonlySet<string> = new Set(),
): SlashCommand[] | null {
  if (!input.startsWith("/")) return null;
  // If the user has typed a space, they're done picking the command.
  if (/\s/.test(input)) return null;
  const query = input.slice(1).toLowerCase();
  return [...SLASH_COMMANDS, ...extra].filter(
    (c) => c.name.startsWith(query) && !excludeNames.has(c.name),
  );
}

/** Names of slash commands that require the GitHub-issue association feature. */
export const GITHUB_SLASH_COMMANDS: ReadonlySet<string> = new Set([
  "gh-update",
  "gh-sync",
  "gh-create",
  "gh-close",
]);
