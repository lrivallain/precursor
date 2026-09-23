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
import { mcpAuthStore } from "../lib/mcpAuth";
import { skillsStore } from "../lib/skillsStore";
import { rolesStore } from "../lib/rolesStore";
import { streamWorkspaceChat } from "../lib/sse";
import { stripSuggestionBlock } from "../lib/suggestions";
import { STOPPED_TOOL_RESULT } from "../lib/toolMeta";
import { useComposerInput } from "../lib/useComposerInput";
import { useResizableHeight } from "../lib/useResizableHeight";
import type { WorkspaceFileRef } from "../lib/workspaceLink";
import { useResizableWidth } from "../lib/useResizableWidth";
import { WORKSPACE_PANES_QUERY, useMediaQuery } from "../lib/useMediaQuery";
import { Z_INDEX } from "../lib/constants";
import { ResizeHandle } from "./ResizeHandle";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { RoleSelector } from "./RoleSelector";
import { Markdown } from "./Markdown";
import { SuggestedReplies } from "./SuggestedReplies";
import { ToolCallBubble } from "./ToolCallBubble";
import type { Workspace, WorkspaceChatMessage } from "../lib/types";

type WorkspaceChatItem =
  | { kind: "user"; content: string }
  | { kind: "assistant"; content: string; suggestions?: string[] }
  | {
      kind: "tool";
      /** The model's tool_call_id, which `tool_result` answers. */
      callId: string;
      name: string;
      arguments: string;
      content: string | null;
      isError: boolean;
      pending: boolean;
      stopped?: boolean;
      link?: WorkspaceFileRef | null;
    };

const CHAT_COLLAPSE_KEY = "precursor:workspace:chat-collapsed";

export function WorkspaceChat({
  area,
  activePath,
  onSetRole,
}: {
  area: Workspace;
  activePath: string | null;
  onSetRole?: (roleId: number | null) => Promise<void>;
}) {
  const [messages, setMessages] = useState<WorkspaceChatItem[]>([]);
  // Autocomplete offers only the commands this surface handles in `send`
  // (skills + whatever the catalog tags `workspace`), so the picker never
  // offers commands the backend rejects.
  const {
    draft: input,
    setDraft: setInput,
    interimText,
    speech,
    suggestions,
    parseCommand,
  } = useComposerInput({ surface: "workspace" });
  const [roleOpen, setRoleOpen] = useState(false);
  const [pending, setPending] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const narrow = useMediaQuery(WORKSPACE_PANES_QUERY);
  // Collapse the assistant into a thin rail (persisted), mirroring the
  // conversation-stats aside on topics/chats. Kept mounted so chat state and
  // any in-flight stream survive a collapse.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    // Too narrow for three panes: start stowed, because expanded the assistant
    // covers the workspace and must not be the state you land on.
    if (window.matchMedia?.(WORKSPACE_PANES_QUERY).matches) return true;
    return window.localStorage.getItem(CHAT_COLLAPSE_KEY) === "1";
  });
  useEffect(() => {
    // A transient choice made on a small screen shouldn't overwrite the
    // preference set where the panel actually fits.
    if (narrow) return;
    window.localStorage.setItem(CHAT_COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed, narrow]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Bumped by Clear so a stream it cut short can't write into the new, empty
  // transcript (its abort path would otherwise append the stopped reply).
  const turnRef = useRef(0);

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
    const cmd = parseCommand(content);
    if (cmd && cmd.name === "role") {
      const arg = cmd.argument.trim();
      if (!arg) {
        setRoleOpen(true);
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
        promptOverride = `${skill.instructions.trim()}\n\n---\n\n${skillsStore.expandReferences(cmd.argument)}`;
      }
    }
    if (promptOverride === undefined) {
      // No leading skill command, but a `/skill-name` may appear mid-prompt.
      const inlined = skillsStore.expandReferences(content);
      if (inlined !== content) promptOverride = inlined;
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
    const turn = ++turnRef.current;
    const current = () => turnRef.current === turn;
    let acc = "";
    let turnSuggestions: string[] = [];
    // Calls of this turn still waiting on their `tool_result`.
    const openCalls = new Set<string>();
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
            if (!current()) return;
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
              // Build the items now: React runs the updater later, after `acc`
              // has been reset below, so reading it in there loses the text.
              const added: WorkspaceChatItem[] = [];
              if (acc.trim()) added.push({ kind: "assistant", content: stripSuggestionBlock(acc) });
              for (const call of calls) {
                openCalls.add(call.id);
                added.push({
                  kind: "tool",
                  callId: call.id,
                  name: call.name,
                  arguments: call.arguments,
                  content: null,
                  isError: false,
                  pending: true,
                });
              }
              setMessages((prev) => [...prev, ...added]);
              acc = "";
              setPending("");
            } else if (e.event === "tool_result") {
              const r = JSON.parse(e.data) as {
                tool_call_id: string;
                content: string;
                is_error: boolean;
                link?: WorkspaceFileRef | null;
              };
              openCalls.delete(r.tool_call_id);
              setMessages((prev) =>
                prev.map((m) =>
                  m.kind === "tool" && m.pending && m.callId === r.tool_call_id
                    ? {
                        ...m,
                        content: r.content,
                        isError: r.is_error,
                        pending: false,
                        link: r.link ?? null,
                      }
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
      if (acc.trim() && current()) {
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
      // A Clear mid-stream wants nothing of this turn in the transcript.
      if (current() && controller.signal.aborted) {
        // Stop rejects the stream before the append above runs. Keep what
        // already streamed, marked the way topics and chats save it.
        const partial = stripSuggestionBlock(acc).trim();
        if (partial) {
          setMessages((m) => [
            ...m,
            { kind: "assistant", content: `${partial}\n\n_(stopped)_` },
          ]);
        }
      } else if (current()) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      // Never leave a call spinning: one Stop cut short is settled as stopped,
      // like topics and chats; one the stream simply never answered, as failed.
      if (openCalls.size > 0 && current()) {
        const stopped = controller.signal.aborted;
        setMessages((prev) =>
          prev.map((m) =>
            m.kind === "tool" && m.pending && openCalls.has(m.callId)
              ? stopped
                ? { ...m, pending: false, stopped: true, content: STOPPED_TOOL_RESULT }
                : {
                    ...m,
                    pending: false,
                    isError: true,
                    content: "The reply ended before the tool returned a result.",
                  }
              : m,
          ),
        );
      }
      setStreaming(false);
      setPending("");
      abortRef.current = null;
    }
  }

  function clearChat(): void {
    // Stop first, so the reply doesn't land in the emptied transcript.
    turnRef.current += 1;
    abortRef.current?.abort();
    setMessages([]);
    setPending("");
    setError(null);
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
      className={
        // Below the three-pane threshold a 24rem side panel leaves no editor,
        // so the expanded assistant covers the workspace instead.
        narrow
          ? `fixed inset-0 flex flex-col min-h-0 bg-bg ${Z_INDEX.SIDEBAR}`
          : "relative shrink-0 border-l border-border flex flex-col min-h-0"
      }
      style={narrow ? undefined : { width: panelWidth }}
    >
      {!narrow && <ResizeHandle onMouseDown={onPanelResize} side="left" />}
      <div className="flex items-center justify-between px-3 h-10 border-b border-border">
        <span className="text-xs font-medium text-muted uppercase tracking-wide">
          Assistant
        </span>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              className="text-xs text-muted hover:text-text"
              onClick={clearChat}
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
              stopped={m.stopped}
              link={m.link}
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
          toolbarStart={
            <>
              <ComposerModelControls />
              <RoleSelector
                value={area.role_id ?? null}
                onChange={(roleId) => void onSetRole?.(roleId)}
                open={roleOpen}
                onOpenChange={setRoleOpen}
              />
            </>
          }
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
