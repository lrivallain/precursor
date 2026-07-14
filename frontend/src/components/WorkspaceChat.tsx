import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Code2,
  Copy,
  Loader2,
  MessageSquare,
} from "lucide-react";
import { parseSlashCommand, SLASH_COMMANDS } from "../lib/commands";
import type { SlashCommand } from "../lib/commands";
import { mcpAuthStore } from "../lib/mcpAuth";
import { skillsStore, useSkills } from "../lib/skillsStore";
import { rolesStore } from "../lib/rolesStore";
import { useSettings } from "../lib/settingsStore";
import { streamWorkspaceChat } from "../lib/sse";
import { stripSuggestionBlock } from "../lib/suggestions";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useResizableWidth } from "../lib/useResizableWidth";
import { ResizeHandle } from "./ResizeHandle";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { Markdown } from "./Markdown";
import { SuggestedReplies } from "./SuggestedReplies";
import { ToolCallBubble } from "./ToolCallBubble";
import type { Workspace, WorkspaceChatMessage } from "../lib/types";

type WorkspaceChatItem =
  | { kind: "user"; content: string }
  | { kind: "assistant"; content: string; suggestions?: string[] }
  | {
      kind: "tool";
      name: string;
      arguments: string;
      content: string | null;
      isError: boolean;
      pending: boolean;
    };

const CHAT_COLLAPSE_KEY = "precursor:workspace:chat-collapsed";

export function WorkspaceChat({
  area,
  activePath,
  onSetRole,
  onOpenRoleSelector,
}: {
  area: Workspace;
  activePath: string | null;
  onSetRole?: (roleId: number | null) => Promise<void>;
  onOpenRoleSelector?: () => void;
}) {
  const [messages, setMessages] = useState<WorkspaceChatItem[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Collapse the assistant into a thin rail (persisted), mirroring the
  // conversation-stats aside on topics/chats. Kept mounted so chat state and
  // any in-flight stream survive a collapse.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(CHAT_COLLAPSE_KEY) === "1";
  });
  useEffect(() => {
    window.localStorage.setItem(CHAT_COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Resizable assistant panel (right) + message composer (bottom).
  const { width: panelWidth, onMouseDown: onPanelResize } = useResizableWidth({
    storageKey: "precursor:workspace:chatWidth",
    defaultWidth: 384,
    min: 280,
    max: 720,
    side: "left",
  });
  const { height: composerHeight, onMouseDown: onComposerResize } =
    useResizableHeight({
      storageKey: "precursor:workspace:chatComposerHeight",
      defaultHeight: 56,
      min: 40,
      max: 320,
    });

  const settings = useSettings();

  // Live speech-to-text (when Azure is configured server-side).
  const [interimText, setInterimText] = useState("");
  const speech = useAzureSpeech({
    onFinalChunk: (text) => {
      const chunk = text.trim();
      if (!chunk) return;
      setInput((d) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk));
      setInterimText("");
    },
    onInterim: setInterimText,
    enabled: settings?.stt_azure_ready ?? false,
    lang: settings?.azure_speech_language || undefined,
  });
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

  // Autocomplete: only the commands this surface actually handles in `send`
  // (skills + `/role`), so the picker never offers commands the backend rejects.
  const skills = useSkills();
  const wsCommands = useMemo<SlashCommand[]>(() => {
    const role = SLASH_COMMANDS.find((c) => c.name === "role");
    const skillCommands: SlashCommand[] = skills
      .filter((s) => s.active)
      .map((s) => ({
        name: s.name,
        label: `/${s.name}`,
        description: s.description ?? "",
        kind: "skill" as const,
        argumentHint: "input",
      }));
    return [...(role ? [role] : []), ...skillCommands];
  }, [skills]);
  const suggestions = useMemo<SlashCommand[]>(() => {
    if (!input.startsWith("/") || /\s/.test(input)) return [];
    const q = input.slice(1).toLowerCase();
    return wsCommands.filter((c) => c.name.startsWith(q));
  }, [input, wsCommands]);
  const userHistory = useMemo(
    () => messages.filter((m) => m.kind === "user").map((m) => m.content),
    [messages],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pending]);

  async function send(explicit?: string): Promise<void> {
    const content = (explicit ?? input).trim();
    if (!content || streaming) return;
    if (explicit === undefined) setInput("");
    setError(null);

    // Skills: a `/skill-name argument` invocation is expanded into the prompt
    // the model receives, while the UI keeps showing the literal command.
    let promptOverride: string | undefined;
    const cmd = parseSlashCommand(
      content,
      skillsStore
        .all()
        .filter((s) => s.active)
        .map((s) => ({
          name: s.name,
          label: `/${s.name}`,
          description: s.description ?? "",
          kind: "skill" as const,
        })),
    );
    if (cmd && cmd.name === "role") {
      const arg = cmd.argument.trim();
      if (!arg) {
        onOpenRoleSelector?.();
        return;
      }
      await rolesStore.ensureLoaded();
      const role = rolesStore.byName(arg);
      if (!role) {
        setError(`Unknown role "${arg}". Manage roles in Settings → Roles.`);
        return;
      }
      try {
        await onSetRole?.(role.is_default ? null : role.id);
      } catch (err) {
        setError(`Role change failed: ${(err as Error).message}`);
      }
      return;
    }
    if (cmd) {
      const skill = skillsStore.byName(cmd.name);
      if (skill) {
        promptOverride = `${skill.instructions.trim()}\n\n---\n\n${cmd.argument}`;
      }
    }

    // History sent to the backend is only the user/assistant text turns.
    const history: WorkspaceChatMessage[] = messages
      .filter(
        (m): m is Extract<WorkspaceChatItem, { kind: "user" | "assistant" }> =>
          m.kind === "user" || m.kind === "assistant",
      )
      .map((m) => ({ role: m.kind, content: m.content }));
    setMessages((m) => [...m, { kind: "user", content }]);
    setStreaming(true);
    setPending("");
    const controller = new AbortController();
    abortRef.current = controller;
    let acc = "";
    let turnSuggestions: string[] = [];
    // Tool items emitted during this turn, keyed by tool_call_id so results can
    // be matched back to the call that produced them.
    const toolItems = new Map<string, WorkspaceChatItem & { kind: "tool" }>();
    try {
      await streamWorkspaceChat(
        area.id,
        {
          content,
          history,
          path: activePath,
          ...(promptOverride ? { prompt_override: promptOverride } : {}),
        },
        {
          signal: controller.signal,
          onEvent: (e) => {
            if (e.event === "delta") {
              const { content: c } = JSON.parse(e.data) as { content: string };
              acc += c;
              setPending(acc);
            } else if (e.event === "tool_calls") {
              // Fold any streamed text so far into an assistant bubble, then
              // append a pending tool bubble per call.
              const { calls } = JSON.parse(e.data) as {
                calls: { id: string; name: string; arguments: string }[];
              };
              setMessages((prev) => {
                const next = [...prev];
                if (acc.trim())
                  next.push({ kind: "assistant", content: stripSuggestionBlock(acc) });
                for (const call of calls) {
                  const item: WorkspaceChatItem & { kind: "tool" } = {
                    kind: "tool",
                    name: call.name,
                    arguments: call.arguments,
                    content: null,
                    isError: false,
                    pending: true,
                  };
                  toolItems.set(call.id, item);
                  next.push(item);
                }
                return next;
              });
              acc = "";
              setPending("");
            } else if (e.event === "tool_result") {
              const r = JSON.parse(e.data) as {
                tool_call_id: string;
                content: string;
                is_error: boolean;
              };
              setMessages((prev) =>
                prev.map((m) =>
                  m.kind === "tool" && m === toolItems.get(r.tool_call_id)
                    ? { ...m, content: r.content, isError: r.is_error, pending: false }
                    : m,
                ),
              );
            } else if (e.event === "suggestions") {
              const { items } = JSON.parse(e.data) as { items?: string[] };
              turnSuggestions = items ?? [];
            } else if (e.event === "mcp_auth_required") {
              const { server, message } = JSON.parse(e.data) as {
                server: string;
                message: string;
              };
              mcpAuthStore.report(server ?? "workiq", message ?? "Sign-in required.");
            } else if (e.event === "system") {
              const { message } = JSON.parse(e.data) as { message: string };
              setError(message);
            } else if (e.event === "error") {
              const { message } = JSON.parse(e.data) as { message: string };
              setError(message);
            }
          },
        },
      );
      if (acc.trim()) {
        setMessages((m) => [
          ...m,
          {
            kind: "assistant",
            content: stripSuggestionBlock(acc),
            suggestions: turnSuggestions,
          },
        ]);
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setStreaming(false);
      setPending("");
      abortRef.current = null;
    }
  }

  if (collapsed) {
    return (
      <aside className="relative shrink-0 border-l border-border flex flex-col items-center py-2 px-1 w-9">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="p-1.5 rounded hover:bg-surface text-muted"
          data-tooltip="Show assistant"
          aria-label="Show assistant"
        >
          <ChevronLeft size={16} />
        </button>
        <MessageSquare size={16} className="mt-2 text-muted" />
        {streaming && <Loader2 size={14} className="mt-2 animate-spin text-muted" />}
      </aside>
    );
  }

  return (
    <aside
      className="relative shrink-0 border-l border-border flex flex-col min-h-0"
      style={{ width: panelWidth }}
    >
      <ResizeHandle onMouseDown={onPanelResize} side="left" />
      <div className="flex items-center justify-between px-3 h-10 border-b border-border">
        <span className="text-xs font-medium text-muted uppercase tracking-wide">
          Assistant
        </span>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              className="text-xs text-muted hover:text-text"
              onClick={() => setMessages([])}
            >
              Clear
            </button>
          )}
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="p-1 rounded hover:bg-surface text-muted"
            data-tooltip="Hide assistant"
            aria-label="Hide assistant"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-auto p-3 space-y-3">
        {messages.length === 0 && !streaming && (
          <p className="text-sm text-muted">
            Ask for help drafting or improving
            {activePath ? (
              <>
                {" "}
                <code className="px-1 rounded bg-surface text-xs">{activePath}</code>.
              </>
            ) : (
              " your workspace content."
            )}
          </p>
        )}
        {messages.map((m, i) =>
          m.kind === "tool" ? (
            <ToolCallBubble
              key={i}
              name={m.name}
              arguments={m.arguments}
              content={m.content}
              isError={m.isError}
              pending={m.pending}
            />
          ) : (
            <ChatTurn key={i} role={m.kind} content={m.content} />
          ),
        )}
        {!streaming &&
          (() => {
            const last = messages[messages.length - 1];
            if (last?.kind === "assistant" && last.suggestions?.length) {
              return (
                <SuggestedReplies
                  items={last.suggestions}
                  onPick={(t) => void send(t)}
                  disabled={streaming}
                />
              );
            }
            return null;
          })()}
        {streaming && pending && (
          <ChatTurn role="assistant" content={stripSuggestionBlock(pending)} pending />
        )}
        {error && <p className="text-sm text-red-500">{error}</p>}
      </div>
      <div className="border-t border-border p-2">
        <Composer
          value={input}
          onChange={setInput}
          onSend={() => void send()}
          onStop={() => abortRef.current?.abort()}
          streaming={streaming}
          suggestions={suggestions}
          userHistory={userHistory}
          speech={speech}
          interimText={interimText}
          height={composerHeight}
          onResizeStart={onComposerResize}
          placeholder={activePath ? `Improve ${activePath}…` : "Ask the assistant…"}
          toolbarStart={<ComposerModelControls />}
        />
      </div>
    </aside>
  );
}

function ChatTurn({
  role,
  content,
  pending,
}: {
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const [copied, setCopied] = useState<null | "text" | "md">(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // Copy the rendered text (markdown stripped) or the raw markdown source —
  // mirrors the main chat's MessageBubble actions.
  const copyTo = async (kind: "text" | "md") => {
    const value =
      kind === "md" ? content : (contentRef.current?.textContent ?? content).trim();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  };

  const showActions = role === "assistant" && !pending && !!content;

  return (
    <div
      className={`group relative rounded-lg px-3 py-2 text-sm ${
        role === "user" ? "bg-accent/10 ml-6" : "bg-surface mr-6"
      }`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div ref={contentRef}>
        <Markdown className="leading-relaxed">{content || "\u200B"}</Markdown>
      </div>
      {pending && <span className="text-[11px] text-muted italic">streaming…</span>}
      {showActions && (
        <div
          style={{ opacity: hover ? 1 : 0, transition: "opacity 120ms ease-out" }}
          className="absolute -bottom-3 right-2 z-10 flex items-center gap-1 rounded-full border border-border bg-surface px-1 py-0.5 shadow-sm"
        >
          <button
            type="button"
            onClick={() => copyTo("text")}
            className="p-1 rounded-full text-muted hover:text-accent"
            aria-label="Copy message"
            data-tooltip="Copy message"
          >
            {copied === "text" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Copy size={12} />
            )}
          </button>
          <button
            type="button"
            onClick={() => copyTo("md")}
            className="p-1 rounded-full text-muted hover:text-accent"
            aria-label="Copy raw markdown"
            data-tooltip="Copy raw markdown"
          >
            {copied === "md" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Code2 size={12} />
            )}
          </button>
        </div>
      )}
    </div>
  );
}
