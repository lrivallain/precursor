/**
 * Slash-command registry for the chat composer.
 *
 * Built-in commands are listed here; user-defined skills are appended at
 * runtime via the `extra` argument passed by ChatPanel.
 *
 * Scheduled topics run headlessly on the backend, which dispatches these same
 * commands in `precursor/backend/services/scheduled_commands.py`. When adding a
 * new `topic`-surface command, mirror it in that module's
 * `BUILTIN_TOPIC_COMMANDS` set and add a handler so it works from a schedule.
 */

/**
 * Surfaces a slash command can be used on. Each composer derives its handled /
 * offered command set from these tags (see {@link commandsForSurface}), so the
 * picker and the dispatcher can't drift from the catalog:
 *  - `topic`: the topic chat composer (ChatPanel)
 *  - `chat`:  the flat chat-session composer (ChatSessionPanel)
 *  - `agent`: the agent-session composer (AgentView), intercepted by the backend
 */
export type CommandSurface = "topic" | "chat" | "agent";

export interface SlashCommand {
  name: string;
  label: string;
  description: string;
  /** Free-form hint for what comes after the command name. */
  argumentHint?: string;
  /** Marks commands that come from user-defined skills (vs. built-ins). */
  kind?: "builtin" | "skill";
  /** Composers this command applies to. Omitted for runtime skills. */
  surfaces?: CommandSurface[];
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "gh-update",
    label: "/gh-update",
    description:
      "Draft a status comment on the linked GitHub issue. The text after the command is an instruction (e.g. 'ask the owner for an ETA').",
    argumentHint: "instruction (optional)",
    kind: "builtin",
    surfaces: ["topic"],
  },
  {
    name: "gh-sync",
    label: "/gh-sync",
    description:
      "Force-refresh the linked GitHub issue. Invalidates the cached context so the Context tab regenerates next time.",
    kind: "builtin",
    surfaces: ["topic"],
  },
  {
    name: "gh-create",
    label: "/gh-create",
    description:
      "Create a new GitHub issue from this conversation and link it to the topic. Editable title + body before posting.",
    argumentHint: "instruction (optional)",
    kind: "builtin",
    surfaces: ["topic"],
  },
  {
    name: "gh-close",
    label: "/gh-close",
    description:
      "Close the linked GitHub issue, optionally with a closing comment. Editable before posting.",
    argumentHint: "instruction (optional)",
    kind: "builtin",
    surfaces: ["topic"],
  },
  {
    name: "notes",
    label: "/notes",
    description:
      "Open a scratch pad to capture freeform notes (e.g. during a meeting). Then choose: rephrase via AI, post as a GitHub comment, or add to the chat with or without an AI follow-up.",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "rename",
    label: "/rename",
    description:
      "Rename this conversation. The text after the command becomes the new title.",
    argumentHint: "new title",
    kind: "builtin",
    surfaces: ["topic", "chat", "agent"],
  },
  {
    name: "new",
    label: "/new",
    description:
      "Create a new topic nested under this one and switch to it. The text after the command is its title.",
    argumentHint: "title",
    kind: "builtin",
    surfaces: ["topic"],
  },
  {
    name: "pin",
    label: "/pin",
    description: "Pin this conversation to the top of the sidebar.",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "unpin",
    label: "/unpin",
    description: "Remove this conversation from the pinned list.",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "reminder",
    label: "/reminder",
    description:
      "Schedule a reminder that resurfaces this conversation at a chosen date and time. One per conversation — setting a new one replaces it.",
    argumentHint: "note (optional)",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "reminder-cancel",
    label: "/reminder-cancel",
    description: "Cancel the pending reminder on this conversation.",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "done",
    label: "/done",
    description:
      "Mark a fired reminder as handled, removing it from the Reminders section.",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "clear",
    label: "/clear",
    description:
      "Erase the entire transcript for this conversation (asks for confirmation).",
    kind: "builtin",
    surfaces: ["topic", "chat", "agent"],
  },
  {
    name: "role",
    label: "/role",
    description:
      "Set the assistant role (persona) for this conversation. Pass a role name to switch directly, or run it bare to open the role picker.",
    argumentHint: "role name (optional)",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "archive",
    label: "/archive",
    description:
      "Archive this conversation and leave it. Restore it any time from the archive (your profile menu in the sidebar).",
    kind: "builtin",
    surfaces: ["topic", "chat", "agent"],
  },
  {
    name: "agent",
    label: "/agent",
    description:
      "Start an agent session from this topic with the text as its task. Pass an existing session id (UUID) first (e.g. '/agent 3f2a… keep going') to send the rest as a follow-up to that session instead.",
    argumentHint: "prompt — or: session-uuid prompt",
    kind: "builtin",
    surfaces: ["topic"],
  },
  {
    name: "memory-store",
    label: "/memory-store",
    description:
      "Save a long-term memory injected into every future conversation. Optionally lead with a [kind] tag, e.g. '/memory-store [preference] I prefer concise answers'.",
    argumentHint: "[kind] content",
    kind: "builtin",
    surfaces: ["topic", "chat", "agent"],
  },
  {
    name: "memory-list",
    label: "/memory-list",
    description:
      "List long-term memories with their ids (and kinds) so you can pick one to edit with /memory-update.",
    kind: "builtin",
    surfaces: ["topic", "chat"],
  },
  {
    name: "memory-update",
    label: "/memory-update",
    description:
      "Edit an existing long-term memory by id (see /memory-list or Settings → Memory). Optionally change its [kind] too, e.g. '/memory-update 3 [fact] Updated text'.",
    argumentHint: "<id> [kind] content",
    kind: "builtin",
    surfaces: ["topic", "chat", "agent"],
  },
];

/**
 * Names of built-in commands available on a surface. Derive each composer's
 * handled-command set from this so it always tracks {@link SLASH_COMMANDS}.
 */
export function commandsForSurface(surface: CommandSurface): ReadonlySet<string> {
  return new Set(
    SLASH_COMMANDS.filter((c) => c.surfaces?.includes(surface)).map((c) => c.name),
  );
}

/**
 * Complement of {@link commandsForSurface}: built-in commands NOT available on a
 * surface. Pass to the picker/parser as `excludeNames` so a composer never
 * offers a command it can't handle (skills are added separately and unaffected).
 */
export function surfaceExcludes(surface: CommandSurface): ReadonlySet<string> {
  return new Set(
    SLASH_COMMANDS.filter((c) => !c.surfaces?.includes(surface)).map((c) => c.name),
  );
}

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
 * Recognise a built-in (non-skill) slash command at the start of a *persisted*
 * message, so the transcript can render the command name as a pill with the
 * arguments as plain text. Unlike {@link parseSlashCommand} this isn't
 * surface-scoped and ignores skills — it only matches the built-in catalog.
 */
export function matchBuiltinCommand(
  content: string,
): { name: string; argument: string } | null {
  const match = content.match(/^\/([a-z][a-z0-9-]*)(?:\s+([\s\S]*))?$/i);
  if (!match) return null;
  const name = match[1].toLowerCase();
  if (!SLASH_COMMANDS.some((c) => c.name === name && c.kind !== "skill")) {
    return null;
  }
  return { name, argument: (match[2] ?? "").trim() };
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

/**
 * Slash commands available inside an agent session. Everything else is disabled
 * there (the backend rejects unknown commands instead of forwarding them to the
 * SDK), so the composer only offers these. Derived from {@link SLASH_COMMANDS}
 * so it can't drift from the catalog.
 */
export const AGENT_SLASH_COMMANDS: ReadonlySet<string> = commandsForSurface("agent");

/**
 * Autocomplete matcher for the agent composer: like {@link matchSlashCommands}
 * but restricted to {@link AGENT_SLASH_COMMANDS} (no skills, no other builtins).
 */
export function matchAgentSlashCommands(input: string): SlashCommand[] | null {
  if (!input.startsWith("/")) return null;
  if (/\s/.test(input)) return null;
  const query = input.slice(1).toLowerCase();
  return SLASH_COMMANDS.filter(
    (c) => AGENT_SLASH_COMMANDS.has(c.name) && c.name.startsWith(query),
  );
}

const MEMORY_BRACKET_KIND_RE = /^\[([^\]]*)\]\s*([\s\S]*)$/;

/**
 * Peel an optional leading `[kind]` token off a memory command argument.
 * Returns `{ kind, content }`; `kind` is `null` when no bracket prefix is given.
 * The kind is returned verbatim — the backend normalises/validates it.
 */
function splitMemoryKind(argument: string): { kind: string | null; content: string } {
  const match = argument.trim().match(MEMORY_BRACKET_KIND_RE);
  if (!match) return { kind: null, content: argument.trim() };
  return { kind: match[1].trim(), content: match[2].trim() };
}

export interface ParsedMemoryStore {
  kind: string;
  content: string;
}

/**
 * Parse a `/memory-store [kind] content` argument. Returns `null` when there's
 * no content to store (the caller surfaces a usage hint).
 */
export function parseMemoryStoreArg(argument: string): ParsedMemoryStore | null {
  const { kind, content } = splitMemoryKind(argument);
  if (!content) return null;
  return { kind: kind || "context", content };
}

export interface ParsedMemoryUpdate {
  id: number;
  kind?: string;
  content?: string;
}

/**
 * Parse a `/memory-update <id> [kind] content` argument. Returns `null` when the
 * id is missing/non-numeric or nothing is provided to change.
 */
export function parseMemoryUpdateArg(argument: string): ParsedMemoryUpdate | null {
  const trimmed = argument.trim();
  const space = trimmed.indexOf(" ");
  const head = space === -1 ? trimmed : trimmed.slice(0, space);
  const tail = space === -1 ? "" : trimmed.slice(space + 1);
  if (!/^\d+$/.test(head)) return null;
  const id = Number(head);
  const { kind, content } = splitMemoryKind(tail);
  if (!content && kind === null) return null;
  const out: ParsedMemoryUpdate = { id };
  if (kind) out.kind = kind;
  if (content) out.content = content;
  return out;
}

/**
 * Render long-term memories as a chat system-note. Shared by the topic and chat
 * `/memory-list` handlers so the listing reads the same in both. Renders a
 * GitHub-flavoured Markdown table so ids (needed by `/memory-update`), kinds, and
 * content stay aligned and readable.
 */
export function formatMemoryList(
  memories: ReadonlyArray<{ id: number; kind: string; content: string }>,
): string {
  if (memories.length === 0) {
    return "No memories yet. Add one with `/memory-store [kind] <content>`.";
  }
  const cell = (value: string) => value.replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
  const rows = memories.map((m) => `| #${m.id} | ${cell(m.kind)} | ${cell(m.content)} |`);
  return [
    "**Long-term memories** — edit with `/memory-update <id> [kind] <content>`:",
    "",
    "| ID | Kind | Memory |",
    "| --- | --- | --- |",
    ...rows,
  ].join("\n");
}

/**
 * Monotonically-decreasing ids for client-only transcript rows (command echoes
 * and system notes). Keeps React keys unique even when several are appended in
 * the same millisecond, where `-Date.now()` alone would collide.
 */
let syntheticIdSeq = 0;
export function nextSyntheticMessageId(): number {
  syntheticIdSeq += 1;
  return -(Date.now() * 1000 + syntheticIdSeq);
}
