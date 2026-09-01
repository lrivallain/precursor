import type {
  AgentArtifact,
  AgentArtifactCreate,
  AgentBlueprint,
  AgentBlueprintCreate,
  AgentBlueprintInstantiate,
  AgentBlueprintUpdate,
  AgentEvent,
  AgentInboxItem,
  AgentLink,
  AgentMetrics,
  AgentModelInfo,
  AgentPermissionDecisionValue,
  AgentPermissionGrant,
  AgentRun,
  AgentRuntimeStatus,
  AgentSchedule,
  AgentApprovalPolicy,
  AgentScheduleCreate,
  AgentScheduleUpdate,
  AgentSession,
  AgentSessionCreate,
  AgentState,
  AgentTrigger,
  AgentTriggerCreate,
  AppVersion,
  BackupRunResult,
  Attachment,
  Chat,
  ChatCreate,
  ChatUpdate,
  CommentDraft,
  CommentPostResult,
  DrawioStatus,
  GhCloseResult,
  GhCreateDraft,
  GhCreatePostResult,
  GhSyncResult,
  FileDiff,
  GitActionResult,
  GitHubIssue,
  GitStatus,
  IssueComment,
  IssueLabel,
  IssuePushResult,
  IssueSummary,
  LLMModel,
  LLMProviderSpec,
  LocalPath,
  MCPServerCreate,
  MCPServerStatus,
  MCPServerUpdate,
  McpAuthDiagnostics,
  Me,
  Memory,
  MemoryCreate,
  MemoryUpdate,
  AgendaEvent,
  AgendaResponse,
  MeetingAttachment,
  MeetingInsight,
  MeetingSegment,
  MeetingSegmentCreate,
  MeetingSegmentUpdate,
  MeetingSession,
  MeetingSessionCreate,
  MeetingSessionUpdate,
  Message,
  NotesDraft,
  NoteDraftAttachment,
  InstalledPlugin,
  PluginDescriptor,
  PluginEnvironment,
  IssueDetail,
  Reminder,
  ReminderContainer,
  ReminderCreate,
  ReminderItem,
  RefineRequest,
  RefineResponse,
  Collection,
  CollectionCreate,
  CollectionUpdate,
  CopilotQuota,
  Role,
  RoleCreate,
  RoleUpdate,
  Schedule,
  ScheduleUpdate,
  TopicScheduleCreate,
  SearchResponse,
  Settings,
  SettingsUpdate,
  Skill,
  SkillCreate,
  SkillUpdate,
  SystemStats,
  CleanupPreview,
  CleanupRunResult,
  CompactResult,
  Topic,
  TopicNode,
  TransferImportResult,
  TransferPreview,
  TransferResolution,
  UsageStats,
  Workflow,
  WorkflowCreate,
  WorkflowRun,
  WorkflowScheduleUpdate,
  WorkflowState,
  WorkflowStepRejectPolicy,
  WorkflowSummary,
  WorkflowUpdate,
  Workspace,
  WorkspaceCreate,
  WorkspaceFileContent,
  WorkspaceFileNode,
  WorkspaceUpdate,
} from "./types";
import { CLIENT_ID } from "./clientId";

/**
 * Issue a JSON API request with the shared headers + error unwrapping. Exported
 * so plugin bundles (`src/plugins/*`) can call their own backend routes without
 * re-implementing the transport — see `plugins/kanban/api.ts`.
 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": CLIENT_ID,
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    throw await httpError(res);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// Pull a human-readable message out of an error response body. FastAPI wraps
// errors as `{"detail": "..."}` (or, for validation errors, a list of
// `{"msg", "loc"}` objects); return just that so the UI never shows the raw JSON
// envelope. Returns null when the body carries no usable detail.
function parseDetail(body: string): string | null {
  if (!body) return null;
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string") return detail.trim() || null;
      if (Array.isArray(detail)) {
        const msgs = detail
          .map((d) =>
            d && typeof d === "object" && typeof (d as { msg?: unknown }).msg === "string"
              ? (d as { msg: string }).msg
              : null,
          )
          .filter((m): m is string => m !== null);
        if (msgs.length) return msgs.join("; ");
      }
    }
  } catch {
    /* body isn't JSON — no detail to extract */
  }
  return null;
}

// Build an Error from a failed response: prefer the server's `detail` message
// (clean, user-facing) and fall back to the HTTP status when there's none.
async function httpError(res: Response): Promise<Error> {
  const body = await res.text().catch(() => "");
  const detail = parseDetail(body);
  if (detail) return new Error(detail);
  return new Error(body ? `${res.status} ${res.statusText}: ${body}` : `${res.status} ${res.statusText}`);
}

// FastAPI errors surface through `request`/`postForm` already reduced to their
// `detail` message. This helper stays as a safety net for any Error whose
// message still carries the "<status> <statusText>: <body>" envelope (e.g. from
// other call sites): it strips the prefix and returns the JSON detail if present.
export function apiErrorMessage(e: unknown, fallback = "Something went wrong"): string {
  if (!(e instanceof Error)) return fallback;
  const idx = e.message.indexOf(": ");
  const body = idx >= 0 ? e.message.slice(idx + 2) : e.message;
  return parseDetail(body) ?? (e.message || fallback);
}

// Multipart POST for single-file uploads. Shared by every attachment endpoint,
// which differ only in URL and response shape.
async function postForm<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(path, {
    method: "POST",
    headers: { "X-Client-Id": CLIENT_ID },
    body: form,
  });
  if (!res.ok) {
    throw await httpError(res);
  }
  return (await res.json()) as T;
}

/** Options for windowed (cursor-paginated) message listing. */
export interface MessageWindow {
  /** Max rows to return — the server caps this. Omit for the full transcript. */
  limit?: number;
  /** Return rows older than this message id (the oldest one already loaded). */
  beforeId?: number;
}

function messageWindowQuery(opts?: MessageWindow): string {
  if (!opts) return "";
  const params = new URLSearchParams();
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.beforeId != null) params.set("before_id", String(opts.beforeId));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export const api = {
  topics: {
    // Topics
    list: (q?: string, collectionId?: number | null) => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (collectionId != null) params.set("collection_id", String(collectionId));
      const qs = params.toString();
      return request<Topic[]>(`/api/topics${qs ? `?${qs}` : ""}`);
    },
    tree: (collectionId?: number | null) =>
      request<TopicNode[]>(
        collectionId == null
          ? `/api/topics/tree`
          : `/api/topics/tree?collection_id=${collectionId}`,
      ),
    get: (id: number) => request<Topic>(`/api/topics/${id}`),
    getBySlug: (slug: string) =>
      request<Topic>(`/api/topics/by-slug/${encodeURIComponent(slug)}`),
    // Resolve the immutable `/t/<uuid>` permalink.
    getByPublicId: (publicId: string) =>
      request<Topic>(`/api/topics/by-public-id/${encodeURIComponent(publicId)}`),
    create: (data: Partial<Topic> & { create_linked_issue?: boolean }) =>
      request<Topic>(`/api/topics`, { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Topic>) =>
      request<Topic>(`/api/topics/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: number) => request<void>(`/api/topics/${id}`, { method: "DELETE" }),
    markRead: (id: number) =>
      request<void>(`/api/topics/${id}/read`, { method: "POST" }),
    markUnread: (id: number) =>
      request<void>(`/api/topics/${id}/unread`, { method: "POST" }),
    listArchived: (collectionId?: number | null) =>
      request<Topic[]>(
        collectionId == null
          ? `/api/topics/archived`
          : `/api/topics/archived?collection_id=${collectionId}`,
      ),
    archive: (id: number) =>
      request<Topic>(`/api/topics/${id}/archive`, { method: "POST" }),
    unarchive: (id: number) =>
      request<Topic>(`/api/topics/${id}/unarchive`, { method: "POST" }),

    // Topic schedules (run a topic's prompt on a recurrence). Keyed by topic id;
    // mirror the agent schedule endpoints.
    getSchedule: (topicId: number) =>
      request<Schedule>(`/api/topics/${topicId}/schedule`),
    createSchedule: (topicId: number, data: TopicScheduleCreate) =>
      request<Schedule>(`/api/topics/${topicId}/schedule`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateSchedule: (topicId: number, data: ScheduleUpdate) =>
      request<Schedule>(`/api/topics/${topicId}/schedule`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    deleteSchedule: (topicId: number) =>
      request<void>(`/api/topics/${topicId}/schedule`, { method: "DELETE" }),
    runScheduleNow: (topicId: number) =>
      request<Schedule>(`/api/topics/${topicId}/schedule/run`, { method: "POST" }),
  },

  chats: {
    // Chats (flat conversation sessions — no tree, no GitHub link)
    list: (q?: string) =>
      request<Chat[]>(`/api/chats${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    listArchived: () => request<Chat[]>(`/api/chats/archived`),
    get: (id: number) => request<Chat>(`/api/chats/${id}`),
    getBySlug: (slug: string) =>
      request<Chat>(`/api/chats/by-slug/${encodeURIComponent(slug)}`),
    create: (data: ChatCreate) =>
      request<Chat>(`/api/chats`, { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: ChatUpdate) =>
      request<Chat>(`/api/chats/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: number) => request<void>(`/api/chats/${id}`, { method: "DELETE" }),
    markRead: (id: number) => request<void>(`/api/chats/${id}/read`, { method: "POST" }),
    markUnread: (id: number) =>
      request<void>(`/api/chats/${id}/unread`, { method: "POST" }),
    archive: (id: number) =>
      request<Chat>(`/api/chats/${id}/archive`, { method: "POST" }),
    unarchive: (id: number) =>
      request<Chat>(`/api/chats/${id}/unarchive`, { method: "POST" }),
    // Promote a flat chat into a full topic (moves the transcript over). Chats
    // have no collection, so the caller names the one it is looking at.
    promote: (id: number, collectionId?: number | null) =>
      request<Topic>(
        collectionId == null
          ? `/api/chats/${id}/promote`
          : `/api/chats/${id}/promote?collection_id=${collectionId}`,
        { method: "POST" },
      ),

    // Chat messages (mirror topic message endpoints)
    listMessages: (chatId: number, opts?: MessageWindow) =>
      request<Message[]>(`/api/chats/${chatId}/messages${messageWindowQuery(opts)}`),
    clearMessages: (chatId: number) =>
      request<void>(`/api/chats/${chatId}/messages`, { method: "DELETE" }),
    deleteMessage: (chatId: number, messageId: number) =>
      request<void>(`/api/chats/${chatId}/messages/${messageId}`, { method: "DELETE" }),
    saveStoppedMessage: (chatId: number, content: string) =>
      request<Message>(`/api/chats/${chatId}/messages/stopped`, {
        method: "POST",
        body: JSON.stringify({ content }),
      }),
    // /notes for chats (no GitHub comment option)
    rephraseNotes: (chatId: number, text: string, instruction?: string) =>
      request<{ text: string }>(`/api/chats/${chatId}/messages/notes/rephrase`, {
        method: "POST",
        body: JSON.stringify({ text, instruction: instruction ?? null }),
      }),
    appendNotes: (chatId: number, text: string, attachmentIds: number[] = []) =>
      request<{ message: Message }>(`/api/chats/${chatId}/messages/notes/append`, {
        method: "POST",
        body: JSON.stringify({ text, attachment_ids: attachmentIds }),
      }),
    getNotesDraft: (chatId: number) =>
      request<NotesDraft>(`/api/chats/${chatId}/messages/notes/draft`),
    saveNotesDraft: (chatId: number, text: string) =>
      request<NotesDraft>(`/api/chats/${chatId}/messages/notes/draft`, {
        method: "PUT",
        body: JSON.stringify({ text }),
      }),
    clearNotesDraft: (chatId: number) =>
      request<void>(`/api/chats/${chatId}/messages/notes/draft`, { method: "DELETE" }),
    listNoteAttachments: (chatId: number) =>
      request<NoteDraftAttachment[]>(`/api/chats/${chatId}/messages/notes/attachments`),
    uploadNoteAttachment: (chatId: number, file: File): Promise<NoteDraftAttachment> =>
      postForm<NoteDraftAttachment>(
        `/api/chats/${chatId}/messages/notes/attachments`,
        file,
      ),
    deleteNoteAttachment: (chatId: number, attachmentId: number) =>
      request<void>(`/api/chats/${chatId}/messages/notes/attachments/${attachmentId}`, {
        method: "DELETE",
      }),
  },

  reminders: {
    // Reminders (one-shot date/time). Keyed by container kind + id; shared by
    // topics and chats. listReminders returns only fired (awaiting acknowledgment).
    list: () => request<ReminderItem[]>(`/api/reminders`),
    // Resolves to null when the conversation has no reminder (a 200, not a 404).
    get: (container: ReminderContainer, id: number) =>
      request<Reminder | null>(`/api/reminders/${container}/${id}`),
    set: (container: ReminderContainer, id: number, data: ReminderCreate) =>
      request<Reminder>(`/api/reminders/${container}/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    // Used for both /reminder-cancel (pending) and /done (fired) — both delete.
    clear: (container: ReminderContainer, id: number) =>
      request<void>(`/api/reminders/${container}/${id}`, { method: "DELETE" }),
  },

  agents: {
    // Agents mode (Copilot SDK). Long-running agent sessions, optionally attached
    // to a topic/chat. Live progress arrives via the `agent.changed` SSE event;
    // the step timeline is re-fetched from `/events` on each signal.
    list: (filter?: { topicId?: number; chatId?: number }) => {
      const qs = new URLSearchParams();
      if (filter?.topicId != null) qs.set("topic_id", String(filter.topicId));
      if (filter?.chatId != null) qs.set("chat_id", String(filter.chatId));
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return request<AgentSession[]>(`/api/agents${suffix}`);
    },
    get: (id: number | string) => request<AgentSession>(`/api/agents/${id}`),
    markRead: (id: number | string) =>
      request<void>(`/api/agents/${id}/read`, { method: "POST" }),
    markUnread: (id: number | string) =>
      request<void>(`/api/agents/${id}/unread`, { method: "POST" }),
    create: (data: AgentSessionCreate) =>
      request<AgentSession>(`/api/agents`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    // `agentRunId` narrows the transcript to one execution — without it a
    // reusable agent driven by two workflows at once reads as one conversation.
    getEvents: (id: number, agentRunId?: number | null) =>
      request<AgentEvent[]>(
        `/api/agents/${id}/events${agentRunId != null ? `?agent_run_id=${agentRunId}` : ""}`,
      ),
    listModels: () => request<AgentModelInfo[]>(`/api/agents/models`),
    // Runtime capability + provisioning. Unlike the rest of this namespace these
    // stay reachable when the runtime is down — they are how it gets fixed.
    getRuntime: () => request<AgentRuntimeStatus>(`/api/agents/runtime`),
    installCli: () =>
      request<AgentRuntimeStatus>(`/api/agents/runtime/cli`, { method: "POST" }),
    restartForRuntime: () =>
      request<void>(`/api/agents/runtime/restart`, { method: "POST" }),
    listPermissions: () => request<AgentPermissionGrant[]>(`/api/agents/permissions`),
    resetPermissions: () =>
      request<{ cleared: number }>(`/api/agents/permissions/reset`, { method: "POST" }),
    send: (id: number | string, message: string) =>
      request<AgentSession>(`/api/agents/${id}/send`, {
        method: "POST",
        body: JSON.stringify({ message }),
      }),
    cancel: (id: number) =>
      request<AgentSession>(`/api/agents/${id}/cancel`, { method: "POST" }),
    start: (id: number | string) =>
      request<AgentSession>(`/api/agents/${id}/start`, { method: "POST" }),
    resume: (id: number | string) =>
      request<AgentSession>(`/api/agents/${id}/resume`, { method: "POST" }),
    resolvePermission: (
      id: number,
      requestId: string,
      decision: AgentPermissionDecisionValue,
    ) =>
      request<AgentSession>(`/api/agents/${id}/permission`, {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, decision }),
      }),
    link: (id: number, link: AgentLink) =>
      request<AgentSession>(`/api/agents/${id}/link`, {
        method: "PATCH",
        body: JSON.stringify({ topic_id: link.topic_id ?? null, chat_id: link.chat_id ?? null }),
      }),
    rename: (id: number, title: string) =>
      request<AgentSession>(`/api/agents/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      }),
    update: (
      id: number,
      payload: {
        title?: string;
        task?: string;
        role_id?: number | null;
        autonomy_enabled?: boolean;
        max_steps?: number;
        approval_policy?: AgentApprovalPolicy | null;
        token_budget?: number | null;
        max_retries?: number;
        use_mcp?: boolean;
        use_skills?: boolean;
        use_memory?: boolean;
        /** Tri-state, so `null` is meaningful ("every enabled server") and is
         *  sent rather than omitted; omit the key entirely to leave unchanged. */
        mcp_servers?: string | null;
      },
    ) =>
      request<AgentSession>(`/api/agents/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    remove: (id: number) => request<void>(`/api/agents/${id}`, { method: "DELETE" }),
    listArchived: () => request<AgentSession[]>(`/api/agents/archived`),
    /** Workflows that reference this agent (archived ones excluded). */
    workflows: (id: number) => request<WorkflowSummary[]>(`/api/agents/${id}/workflows`),
    /**
     * This agent's execution history, newest first. An agent is a reusable
     * definition, so "what did it do, driven by what, and what did that cost"
     * is a per-run question.
     */
    runs: (id: number | string, opts?: { workflowRunId?: number; limit?: number }) => {
      const q = new URLSearchParams();
      if (opts?.workflowRunId != null) q.set("workflow_run_id", String(opts.workflowRunId));
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      const suffix = q.toString() ? `?${q}` : "";
      return request<AgentRun[]>(`/api/agents/${id}/runs${suffix}`);
    },
    run: (id: number | string, runId: number) =>
      request<AgentRun>(`/api/agents/${id}/runs/${runId}`),
    archive: (id: number) =>
      request<AgentSession>(`/api/agents/${id}/archive`, { method: "POST" }),
    unarchive: (id: number) =>
      request<AgentSession>(`/api/agents/${id}/unarchive`, { method: "POST" }),

    // Agent schedules (recurring auto-re-run of an agent's task). Keyed by the
    // agent's id or public uuid; mirror the topic schedule endpoints.
    getSchedule: (id: number | string) =>
      request<AgentSchedule>(`/api/agents/${id}/schedule`),
    createSchedule: (id: number | string, data: AgentScheduleCreate) =>
      request<AgentSchedule>(`/api/agents/${id}/schedule`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateSchedule: (id: number | string, data: AgentScheduleUpdate) =>
      request<AgentSchedule>(`/api/agents/${id}/schedule`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    deleteSchedule: (id: number | string) =>
      request<void>(`/api/agents/${id}/schedule`, { method: "DELETE" }),
    runScheduleNow: (id: number | string) =>
      request<AgentSchedule>(`/api/agents/${id}/schedule/run`, { method: "POST" }),

    // --- Orchestrator: aggregate observability + unified inbox -------------
    // Fleet-wide rollup (status counts, token totals, concurrency headroom).
    metrics: () => request<AgentMetrics>(`/api/agents/metrics`),
    // Everything waiting on a human: raised questions, permission gates, budget parks.
    inbox: () => request<AgentInboxItem[]>(`/api/agents/inbox`),

    // --- Blueprints (reusable agent templates) ----------------------------
    listBlueprints: () => request<AgentBlueprint[]>(`/api/agents/blueprints`),
    createBlueprint: (data: AgentBlueprintCreate) =>
      request<AgentBlueprint>(`/api/agents/blueprints`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateBlueprint: (id: number, data: AgentBlueprintUpdate) =>
      request<AgentBlueprint>(`/api/agents/blueprints/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    deleteBlueprint: (id: number) =>
      request<void>(`/api/agents/blueprints/${id}`, { method: "DELETE" }),
    instantiateBlueprint: (id: number, data: AgentBlueprintInstantiate) =>
      request<AgentSession>(`/api/agents/blueprints/${id}/instantiate`, {
        method: "POST",
        body: JSON.stringify(data),
      }),

    // --- Shared artifacts / blackboard ------------------------------------
    listArtifacts: (id: number) =>
      request<AgentArtifact[]>(`/api/agents/${id}/artifacts`),
    getArtifact: (id: number, artifactId: number) =>
      request<AgentArtifact>(`/api/agents/${id}/artifacts/${artifactId}`),
    // Browser-openable URL for an artifact's raw body (kind-appropriate
    // content-type; a `link` artifact redirects to its URL). Used for the
    // "Open raw" affordance and programmatic/download access.
    rawArtifactUrl: (id: number, artifactId: number) =>
      `/api/agents/${id}/artifacts/${artifactId}/raw`,
    createArtifact: (id: number, data: AgentArtifactCreate) =>
      request<AgentArtifact>(`/api/agents/${id}/artifacts`, {
        method: "POST",
        body: JSON.stringify(data),
      }),

    // --- State (private cross-run scratchpad) -----------------------------
    // Distinct from artifacts: state is private bookkeeping that *survives*
    // re-runs, so this surface is mostly for inspecting (or resetting) a
    // recurring agent's saved cursor.
    listState: (id: number) => request<AgentState[]>(`/api/agents/${id}/state`),
    setState: (id: number, key: string, value: string) =>
      request<AgentState>(`/api/agents/${id}/state`, {
        method: "PUT",
        body: JSON.stringify({ key, value }),
      }),
    deleteState: (id: number, key: string) =>
      request<void>(`/api/agents/${id}/state/${encodeURIComponent(key)}`, {
        method: "DELETE",
      }),
    clearState: (id: number) =>
      request<void>(`/api/agents/${id}/state`, { method: "DELETE" }),

    // --- Triggers (external webhooks) -------------------------------------
    listTriggers: (id: number) =>
      request<AgentTrigger[]>(`/api/agents/${id}/triggers`),
    createTrigger: (id: number, data: AgentTriggerCreate = {}) =>
      request<AgentTrigger>(`/api/agents/${id}/triggers`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    deleteTrigger: (id: number, triggerId: number) =>
      request<void>(`/api/agents/${id}/triggers/${triggerId}`, {
        method: "DELETE",
      }),
  },

  workflows: {
    // Workflows: reusable, named sequences of independent agents. The *workflow*
    // owns the chaining (not agent-to-agent dependencies). Live progress arrives
    // via the `workflow.changed` SSE event.
    list: (opts?: { includeArchived?: boolean }) => {
      const qs = opts?.includeArchived ? "?include_archived=true" : "";
      return request<Workflow[]>(`/api/workflows${qs}`);
    },
    get: (id: number) => request<Workflow>(`/api/workflows/${id}`),
    // Durable run history (newest first), each with its per-step attempt traces.
    runs: (id: number, limit?: number) => {
      const qs = limit ? `?limit=${limit}` : "";
      return request<WorkflowRun[]>(`/api/workflows/${id}/runs${qs}`);
    },
    // The agent's activity for one step *attempt* — tool calls, reasoning,
    // errors. Sliced to the attempt's window, so an agent re-driven several
    // times doesn't replay its whole history under every trace row.
    stepEvents: (id: number, stepRunId: number) =>
      request<AgentEvent[]>(`/api/workflows/${id}/run-steps/${stepRunId}/events`),
    create: (data: WorkflowCreate) =>
      request<Workflow>(`/api/workflows`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: number, data: WorkflowUpdate) =>
      request<Workflow>(`/api/workflows/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    remove: (id: number) => request<void>(`/api/workflows/${id}`, { method: "DELETE" }),
    // Lifecycle controls — the workflow coordinates its step agents.
    run: (id: number, input?: string | null) =>
      request<Workflow>(`/api/workflows/${id}/run`, {
        method: "POST",
        body: JSON.stringify({ input: input?.trim() ? input.trim() : null }),
      }),
    approve: (id: number, note?: string | null) =>
      request<Workflow>(`/api/workflows/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ note: note?.trim() ? note.trim() : null }),
      }),
    reject: (id: number, note?: string | null, action?: WorkflowStepRejectPolicy | null) =>
      request<Workflow>(`/api/workflows/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({
          note: note?.trim() ? note.trim() : null,
          action: action ?? null,
        }),
      }),
    pause: (id: number) => request<Workflow>(`/api/workflows/${id}/pause`, { method: "POST" }),
    // Resume a paused run. `input` answers whatever parked it — when the pause
    // came from a step's agent blocking on a question, re-driving it blind would
    // just hit the same question again.
    resume: (id: number, input?: string | null) =>
      request<Workflow>(`/api/workflows/${id}/resume`, {
        method: "POST",
        body: JSON.stringify({ input: input?.trim() ? input.trim() : null }),
      }),
    // Answer the tool-permission gate parking a step. Goes through the workflow
    // (not the agent) because resolving it must also un-pause the run: the block
    // stopped the coordinator, so an approved agent would otherwise finish its
    // turn into a pipeline that had stopped listening.
    resolvePermission: (
      id: number,
      requestId: string,
      decision: AgentPermissionDecisionValue,
    ) =>
      request<Workflow>(`/api/workflows/${id}/permission`, {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, decision }),
      }),
    // Re-drive one step of a stopped run as a fresh attempt, in place — instead
    // of re-running the whole pipeline and paying for the good steps twice.
    // Omit `position` to retry the step whose failure stopped the run.
    retry: (id: number, opts?: { position?: number | null; input?: string | null }) =>
      request<Workflow>(`/api/workflows/${id}/retry`, {
        method: "POST",
        body: JSON.stringify({
          position: opts?.position ?? null,
          input: opts?.input?.trim() ? opts.input.trim() : null,
        }),
      }),
    cancel: (id: number) => request<Workflow>(`/api/workflows/${id}/cancel`, { method: "POST" }),
    // Run one recorded step attempt again, alone, on the input it first saw.
    // Unlike `retry` this advances nothing — so it works on a run that
    // succeeded, when you just want another take on one step.
    replayStep: (id: number, stepRunId: number) =>
      request<Workflow>(`/api/workflows/${id}/run-steps/${stepRunId}/replay`, {
        method: "POST",
      }),
    listArchived: () => request<Workflow[]>(`/api/workflows/archived`),
    archive: (id: number) => request<Workflow>(`/api/workflows/${id}/archive`, { method: "POST" }),
    unarchive: (id: number) =>
      request<Workflow>(`/api/workflows/${id}/unarchive`, { method: "POST" }),
    // Recurrence config (PUT replaces the whole schedule block).
    setSchedule: (id: number, data: WorkflowScheduleUpdate) =>
      request<Workflow>(`/api/workflows/${id}/schedule`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    // Webhook trigger: mint returns the workflow with `webhook_token` populated.
    mintWebhook: (id: number) =>
      request<Workflow>(`/api/workflows/${id}/webhook`, { method: "POST" }),
    revokeWebhook: (id: number) =>
      request<Workflow>(`/api/workflows/${id}/webhook`, { method: "DELETE" }),
    // Public webhook URL to copy (fires the workflow when POSTed to).
    webhookUrl: (token: string) => `/api/workflows/hooks/${token}`,

    // --- State (the pipeline's own memory) --------------------------------
    // Named values shared by every step and kept across runs. Setting one by
    // hand is how you seed a pipeline's first run (e.g. its starting cursor).
    listState: (id: number) => request<WorkflowState[]>(`/api/workflows/${id}/state`),
    setState: (id: number, key: string, value: string) =>
      request<WorkflowState>(`/api/workflows/${id}/state`, {
        method: "PUT",
        body: JSON.stringify({ key, value }),
      }),
    deleteState: (id: number, key: string) =>
      request<void>(`/api/workflows/${id}/state/${encodeURIComponent(key)}`, {
        method: "DELETE",
      }),
    clearState: (id: number) =>
      request<void>(`/api/workflows/${id}/state`, { method: "DELETE" }),
  },

  transfer: {
    // YAML export/import. Export is a plain download URL so the browser handles
    // the file save; import is two calls because the replace/create/link choice
    // can only be offered once `preview` has reported the collisions.
    exportWorkflowUrl: (id: number) => `/api/transfer/workflows/${id}`,
    exportAgentUrl: (id: number | string) => `/api/transfer/agents/${id}`,
    preview: (content: string) =>
      request<TransferPreview>(`/api/transfer/preview`, {
        method: "POST",
        body: JSON.stringify({ content }),
      }),
    import: (content: string, resolutions: TransferResolution[] = []) =>
      request<TransferImportResult>(`/api/transfer/import`, {
        method: "POST",
        body: JSON.stringify({ content, resolutions }),
      }),
  },

  messages: {
    // Messages
    list: (topicId: number, opts?: MessageWindow) =>
      request<Message[]>(`/api/topics/${topicId}/messages${messageWindowQuery(opts)}`),
    clear: (topicId: number) =>
      request<void>(`/api/topics/${topicId}/messages`, { method: "DELETE" }),
    remove: (topicId: number, messageId: number) =>
      request<void>(`/api/topics/${topicId}/messages/${messageId}`, {
        method: "DELETE",
      }),
    // Persist a partial assistant reply when the user stops generation.
    saveStopped: (topicId: number, content: string) =>
      request<Message>(`/api/topics/${topicId}/messages/stopped`, {
        method: "POST",
        body: JSON.stringify({ content }),
      }),
  },

  attachments: {
    // Attachments (images + selected documents)
    uploadForTopic: (topicId: number, file: File): Promise<Attachment> =>
      postForm<Attachment>(`/api/topics/${topicId}/attachments`, file),
    uploadForChat: (chatId: number, file: File): Promise<Attachment> =>
      postForm<Attachment>(`/api/chats/${chatId}/attachments`, file),
    remove: (attachmentId: number) =>
      request<void>(`/api/attachments/${attachmentId}`, { method: "DELETE" }),
    url: (attachmentId: number) => `/api/attachments/${attachmentId}`,
  },

  settings: {
    // Settings
    get: () => request<Settings>(`/api/settings`),
    update: (data: SettingsUpdate) =>
      request<Settings>(`/api/settings`, { method: "PUT", body: JSON.stringify(data) }),
    runBackupNow: () =>
      request<BackupRunResult>(`/api/settings/backup/run`, { method: "POST" }),
  },

  stt: {
    // Speech-to-text (Azure token broker)
    getToken: () =>
      request<{ token: string; endpoint: string; language: string }>(`/api/stt/token`),
    testConnection: (endpoint: string, key?: string) =>
      request<{ ok: boolean; detail: string | null }>(`/api/stt/test`, {
        method: "POST",
        body: JSON.stringify({ endpoint, key: key || null }),
      }),
  },

  github: {
    // GitHub
    listIssues: (repo?: string, q?: string) => {
      const params = new URLSearchParams();
      if (repo) params.set("repo", repo);
      if (q) params.set("q", q);
      const qs = params.toString();
      return request<GitHubIssue[]>(`/api/github/issues${qs ? `?${qs}` : ""}`);
    },
    createIssue: (data: { repo?: string; title: string; body?: string; labels?: string[] }) =>
      request<GitHubIssue>(`/api/github/issues`, {
        method: "POST",
        body: JSON.stringify(data),
      }),

    getIssue: (number: number, repo?: string) => {
      const qs = repo ? `?repo=${encodeURIComponent(repo)}` : "";
      return request<IssueDetail>(`/api/github/issues/${number}${qs}`);
    },
    addIssueComment: (number: number, body: string, repo?: string) =>
      request<IssueComment>(`/api/github/issues/${number}/comments`, {
        method: "POST",
        body: JSON.stringify({ body, repo }),
      }),
    setIssueLabels: (number: number, labels: string[], repo?: string) =>
      request<IssueLabel[]>(`/api/github/issues/${number}/labels`, {
        method: "PUT",
        body: JSON.stringify({ labels, repo }),
      }),
    listLabels: (repo?: string) => {
      const qs = repo ? `?repo=${encodeURIComponent(repo)}` : "";
      return request<IssueLabel[]>(`/api/github/labels${qs}`);
    },
    // Summaries
    summarizeIssue: (topicId: number, opts: { force?: boolean } = {}) =>
      request<IssueSummary>(
        `/api/topics/${topicId}/summary${opts.force ? "?force=true" : ""}`,
        { method: "POST" },
      ),

    // Push the topic title/description back to the linked GitHub issue.
    pushIssue: (topicId: number) =>
      request<IssuePushResult>(`/api/topics/${topicId}/issue/push`, { method: "POST" }),

    // Slash commands
    draftUpdate: (topicId: number, text?: string) =>
      request<CommentDraft>(`/api/topics/${topicId}/commands/gh-update/draft`, {
        method: "POST",
        body: JSON.stringify({ text: text ?? null }),
      }),
    postUpdate: (topicId: number, body: string, noteAttachmentIds: number[] = []) =>
      request<CommentPostResult>(`/api/topics/${topicId}/commands/gh-update/post`, {
        method: "POST",
        body: JSON.stringify({ body, note_attachment_ids: noteAttachmentIds }),
      }),
    sync: (topicId: number) =>
      request<GhSyncResult>(`/api/topics/${topicId}/commands/gh-sync`, {
        method: "POST",
      }),
    draftCreate: (topicId: number, text?: string) =>
      request<GhCreateDraft>(`/api/topics/${topicId}/commands/gh-create/draft`, {
        method: "POST",
        body: JSON.stringify({ text: text ?? null }),
      }),
    postCreate: (topicId: number, title: string, body: string) =>
      request<GhCreatePostResult>(`/api/topics/${topicId}/commands/gh-create/post`, {
        method: "POST",
        body: JSON.stringify({ title, body }),
      }),
    draftClose: (topicId: number, text?: string) =>
      request<CommentDraft>(`/api/topics/${topicId}/commands/gh-close/draft`, {
        method: "POST",
        body: JSON.stringify({ text: text ?? null }),
      }),
    postClose: (
      topicId: number,
      body: string,
      stateReason: "completed" | "not_planned" = "completed",
    ) =>
      request<GhCloseResult>(`/api/topics/${topicId}/commands/gh-close/post`, {
        method: "POST",
        body: JSON.stringify({ body, state_reason: stateReason }),
      }),
  },

  mcp: {
    // MCP
    list: (probe = true) =>
      request<MCPServerStatus[]>(`/api/mcp/servers?probe=${probe}`),
    probe: (name: string) =>
      request<MCPServerStatus>(`/api/mcp/servers/${name}/probe`, { method: "POST" }),
    connect: (name: string) =>
      request<MCPServerStatus>(`/api/mcp/servers/${name}/connect`, { method: "POST" }),
    disconnect: (name: string) =>
      request<MCPServerStatus>(`/api/mcp/servers/${name}/disconnect`, { method: "POST" }),
    refresh: (name: string) =>
      request<MCPServerStatus>(`/api/mcp/servers/${name}/refresh`, { method: "POST" }),
    setWorkiqPreview: (enabled: boolean) =>
      request<MCPServerStatus>(`/api/mcp/servers/workiq/preview`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      }),
    // Drive the browser sign-in for an OAuth-protected server: the hosted
    // WorkIQ preview, or the Agent 365 workiq-teams / workiq-user endpoints.
    reauthenticateWorkiq: (
      name = "workiq",
      opts?: { usePopup?: boolean; silentOnly?: boolean; auto?: boolean },
    ) => {
      const params = new URLSearchParams();
      if (opts?.usePopup) params.set("use_popup", "true");
      if (opts?.silentOnly) params.set("silent_only", "true");
      if (opts?.auto) params.set("auto", "true");
      const qs = params.toString();
      return request<
        MCPServerStatus & { interaction_required?: boolean; auth_episode?: string | null }
      >(`/api/mcp/servers/${name}/reauthenticate${qs ? `?${qs}` : ""}`, { method: "POST" });
    },
    // Abort an in-flight interactive sign-in so the backend releases that
    // server's fixed OAuth loopback port at once (see workiqSignIn.ts).
    cancelReauthenticateWorkiq: (name = "workiq") =>
      request<{ cancelled: boolean }>(`/api/mcp/servers/${name}/reauthenticate/cancel`, {
        method: "POST",
      }),
    // Everything the backend knows about the WorkIQ credentials — settings in
    // force, per-credential token/idle/state facts, and its own auth trace.
    // Read on demand by ``window.precursorWorkiqAuthReport()``.
    authDiagnostics: (limit = 300) =>
      request<McpAuthDiagnostics>(`/api/mcp/auth/diagnostics?limit=${limit}`),
    create: (data: MCPServerCreate) =>
      request<MCPServerStatus>(`/api/mcp/servers/user`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: number, data: MCPServerUpdate) =>
      request<MCPServerStatus>(`/api/mcp/servers/user/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    remove: (id: number) =>
      request<void>(`/api/mcp/servers/user/${id}`, { method: "DELETE" }),
  },

  llm: {
    // LLM
    listModels: (provider?: string) =>
      request<LLMModel[]>(
        `/api/llm/models${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`,
      ),
    listProviders: () => request<LLMProviderSpec[]>(`/api/llm/providers`),
  },

  notes: {
    // /notes — freeform note capture
    rephrase: (topicId: number, text: string, instruction?: string) =>
      request<{ text: string }>(
        `/api/topics/${topicId}/commands/notes/rephrase`,
        {
          method: "POST",
          body: JSON.stringify({ text, instruction: instruction ?? null }),
        },
      ),
    append: (topicId: number, text: string, attachmentIds: number[] = []) =>
      request<{ message: Message }>(
        `/api/topics/${topicId}/commands/notes/append`,
        { method: "POST", body: JSON.stringify({ text, attachment_ids: attachmentIds }) },
      ),
    getDraft: (topicId: number) =>
      request<NotesDraft>(`/api/topics/${topicId}/commands/notes/draft`),
    saveDraft: (topicId: number, text: string) =>
      request<NotesDraft>(`/api/topics/${topicId}/commands/notes/draft`, {
        method: "PUT",
        body: JSON.stringify({ text }),
      }),
    clearDraft: (topicId: number) =>
      request<void>(`/api/topics/${topicId}/commands/notes/draft`, { method: "DELETE" }),
    listAttachments: (topicId: number) =>
      request<NoteDraftAttachment[]>(`/api/topics/${topicId}/commands/notes/attachments`),
    uploadAttachment: (topicId: number, file: File): Promise<NoteDraftAttachment> =>
      postForm<NoteDraftAttachment>(
        `/api/topics/${topicId}/commands/notes/attachments`,
        file,
      ),
    deleteAttachment: (topicId: number, attachmentId: number) =>
      request<void>(`/api/topics/${topicId}/commands/notes/attachments/${attachmentId}`, {
        method: "DELETE",
      }),
    attachmentUrl: (attachmentId: number) => `/api/notes/attachments/${attachmentId}`,
  },

  plugins: {
    /** Frontend extension descriptors from every enabled plugin. */
    list: () => request<PluginDescriptor[]>(`/api/plugins`),
    /** Every installed plugin, enabled or not, for the Settings panel. */
    installed: () => request<InstalledPlugin[]>(`/api/plugins/installed`),
    setEnabled: (id: string, enabled: boolean) =>
      request<{ id: string; enabled: boolean }>(
        `/api/plugins/installed/${encodeURIComponent(id)}`,
        { method: "PUT", body: JSON.stringify({ enabled }) },
      ),
    /** How to install into this instance (and whether the app may do it). */
    environment: () => request<PluginEnvironment>(`/api/plugins/environment`),
    install: (pkg: string) =>
      request<{ package: string; output: string; restart_required: boolean }>(
        `/api/plugins/install`,
        { method: "POST", body: JSON.stringify({ package: pkg }) },
      ),
    uninstall: (id: string) =>
      request<{ package: string; output: string; restart_required: boolean }>(
        `/api/plugins/installed/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      ),
    /** Restart the server so plugin discovery runs again. */
    restart: () =>
      request<{ status: string }>(`/api/plugins/restart`, { method: "POST" }),
    /** A plugin's own settings blob — opaque to core. */
    settings: {
      get: (id: string) =>
        request<Record<string, unknown>>(
          `/api/plugins/installed/${encodeURIComponent(id)}/settings`,
        ),
      put: (id: string, values: Record<string, unknown>) =>
        request<Record<string, unknown>>(
          `/api/plugins/installed/${encodeURIComponent(id)}/settings`,
          { method: "PUT", body: JSON.stringify(values) },
        ),
    },
  },

  skills: {
    // Skills
    list: () => request<Skill[]>(`/api/skills`),
    create: (data: SkillCreate) =>
      request<Skill>(`/api/skills`, { method: "POST", body: JSON.stringify(data) }),
    update: (name: string, data: SkillUpdate) =>
      request<Skill>(`/api/skills/${encodeURIComponent(name)}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    migrate: (name: string) =>
      request<Skill>(`/api/skills/${encodeURIComponent(name)}/migrate`, {
        method: "POST",
      }),
    remove: (name: string) =>
      request<void>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
    exportUrl: (name: string) =>
      `/api/skills/${encodeURIComponent(name)}/export`,
  },

  roles: {
    // Roles (Assistant personas)
    list: () => request<Role[]>(`/api/roles`),
    create: (data: RoleCreate) =>
      request<Role>(`/api/roles`, { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: RoleUpdate) =>
      request<Role>(`/api/roles/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    remove: (id: number) =>
      request<void>(`/api/roles/${id}`, { method: "DELETE" }),
  },

  collections: {
    // Collections (topic groupings that filter the sidebar tree)
    list: () => request<Collection[]>(`/api/collections`),
    create: (data: CollectionCreate) =>
      request<Collection>(`/api/collections`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: number, data: CollectionUpdate) =>
      request<Collection>(`/api/collections/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    // Topics are never deleted with the collection — they move to `reassignTo`
    // (or the built-in default when omitted).
    remove: (id: number, reassignTo?: number | null) =>
      request<void>(
        reassignTo == null
          ? `/api/collections/${id}`
          : `/api/collections/${id}?reassign_to=${reassignTo}`,
        { method: "DELETE" },
      ),
  },

  memories: {
    // Memories
    list: () => request<Memory[]>(`/api/memories`),
    create: (data: MemoryCreate) =>
      request<Memory>(`/api/memories`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: number, data: MemoryUpdate) =>
      request<Memory>(`/api/memories/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    remove: (id: number) =>
      request<void>(`/api/memories/${id}`, { method: "DELETE" }),
  },

  me: {
    // Current user
    get: () => request<Me>(`/api/me`),
    // Copilot AI-credit usage for the persona menu (null when unavailable).
    copilot: () => request<CopilotQuota | null>(`/api/me/copilot`),
  },

  meetings: {
    // Live meeting sessions
    listSessions: () => request<MeetingSession[]>(`/api/live`),
    getSession: (id: number) =>
      request<MeetingSession>(`/api/live/${id}`),
    createSession: (data: MeetingSessionCreate) =>
      request<MeetingSession>(`/api/live`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateSession: (id: number, data: MeetingSessionUpdate) =>
      request<MeetingSession>(`/api/live/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    deleteSession: (id: number) =>
      request<void>(`/api/live/${id}`, { method: "DELETE" }),
    listArchivedSessions: () => request<MeetingSession[]>(`/api/live/archived`),
    archiveSession: (id: number) =>
      request<MeetingSession>(`/api/live/${id}/archive`, { method: "POST" }),
    unarchiveSession: (id: number) =>
      request<MeetingSession>(`/api/live/${id}/unarchive`, { method: "POST" }),
    listSegments: (id: number) =>
      request<MeetingSegment[]>(`/api/live/${id}/segments`),
    appendSegment: (id: number, data: MeetingSegmentCreate) =>
      request<MeetingSegment>(`/api/live/${id}/segments`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateSegment: (id: number, segmentId: number, data: MeetingSegmentUpdate) =>
      request<MeetingSegment>(`/api/live/${id}/segments/${segmentId}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    renameSpeaker: (id: number, label: string, name: string) =>
      request<MeetingSession>(`/api/live/${id}/speakers`, {
        method: "POST",
        body: JSON.stringify({ label, name }),
      }),
    setAttendees: (id: number, attendees: string[]) =>
      request<MeetingSession>(`/api/live/${id}/attendees`, {
        method: "PUT",
        body: JSON.stringify({ attendees }),
      }),
    addContextNote: (id: number, text: string) =>
      request<MeetingSession>(`/api/live/${id}/context-notes`, {
        method: "POST",
        body: JSON.stringify({ text }),
      }),
    setContextNotes: (id: number, notes: string[]) =>
      request<MeetingSession>(`/api/live/${id}/context-notes`, {
        method: "PUT",
        body: JSON.stringify({ notes }),
      }),
    uploadAttachment: (id: number, file: File): Promise<MeetingAttachment> =>
      postForm<MeetingAttachment>(`/api/live/${id}/attachments`, file),
    ensureChat: (id: number) => request<Chat>(`/api/live/${id}/chat`, { method: "POST" }),
    setFeatures: (id: number, features: string[]) =>
      request<MeetingSession>(`/api/live/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ features }),
      }),
    translate: (id: number, targetLang: string, texts?: string[]) =>
      request<{ text: string; lines: string[]; target_lang: string; model: string }>(
        `/api/live/${id}/translate`,
        {
          method: "POST",
          body: JSON.stringify({ target_lang: targetLang, texts }),
        },
      ),
    listInsights: (id: number) =>
      request<MeetingInsight[]>(`/api/live/${id}/insights`),
    analyze: (id: number) =>
      request<{ insights: MeetingInsight[]; suggestion: string }>(`/api/live/${id}/analyze`, {
        method: "POST",
      }),
    summarize: (id: number) =>
      request<{ summary: string; model: string }>(`/api/live/${id}/summary`, {
        method: "POST",
      }),
    summarizeFromTranscript: (id: number) =>
      request<{ summary: string; model: string }>(
        `/api/live/${id}/summary/from-transcript`,
        { method: "POST" },
      ),
    postSummary: (id: number, summary: string) =>
      request<{
        topic_id: number;
        message_id: number;
        posted_at: string;
        issue_number: number | null;
        issue_comment_url: string | null;
      }>(
        `/api/live/${id}/summary/post`,
        {
          method: "POST",
          body: JSON.stringify({ summary }),
        },
      ),
    topicContextSummary: (id: number) =>
      request<{ summary: string; model: string }>(`/api/live/${id}/topic-summary`, {
        method: "POST",
      }),
    getAgenda: (pastDays = 7) => {
      // The agenda window: from local midnight ``pastDays`` ago through the next
      // local midnight, so past meetings (for a "record from a past meeting"
      // flow) show alongside today's — matched to the user's calendar day and
      // converted to UTC ISO.
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - pastDays);
      const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
      const qs = `?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(
        end.toISOString(),
      )}`;
      return request<AgendaResponse>(`/api/live/m365/agenda${qs}`);
    },
    link: (id: number, event: AgendaEvent) =>
      request<MeetingSession>(`/api/live/${id}/meeting`, {
        method: "POST",
        body: JSON.stringify({
          id: event.id,
          subject: event.subject,
          start: event.start,
          end: event.end,
          organizer: event.organizer,
          attendees: event.attendees,
          is_online: event.is_online,
          join_url: event.join_url,
          body: event.body,
          body_preview: event.body_preview,
        }),
      }),
    unlink: (id: number) =>
      request<MeetingSession>(`/api/live/${id}/meeting`, { method: "DELETE" }),
    postToTopic: (id: number) =>
      request<{ topic_id: number; message_id: number }>(`/api/live/${id}/meeting/post`, {
        method: "POST",
      }),
  },

  workspaces: {
    // Workspaces
    list: () => request<Workspace[]>(`/api/workspaces`),
    create: (data: WorkspaceCreate) =>
      request<Workspace>(`/api/workspaces`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    remove: (id: number) =>
      request<void>(`/api/workspaces/${id}`, { method: "DELETE" }),
    update: (id: number, data: WorkspaceUpdate) =>
      request<Workspace>(`/api/workspaces/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    listFiles: (workspaceId: number) =>
      request<WorkspaceFileNode[]>(`/api/workspaces/${workspaceId}/files`),
    readFile: (workspaceId: number, path: string) =>
      request<WorkspaceFileContent>(
        `/api/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
      ),
    writeFile: (workspaceId: number, path: string, content: string) =>
      request<WorkspaceFileContent>(
        `/api/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
        { method: "PUT", body: JSON.stringify({ content }) },
      ),
    createFile: (workspaceId: number, path: string, content = "") =>
      request<WorkspaceFileContent>(`/api/workspaces/${workspaceId}/file`, {
        method: "POST",
        body: JSON.stringify({ path, content }),
      }),
    createFolder: (workspaceId: number, path: string) =>
      request<WorkspaceFileNode>(`/api/workspaces/${workspaceId}/folder`, {
        method: "POST",
        body: JSON.stringify({ path }),
      }),
    deleteFile: (workspaceId: number, path: string) =>
      request<void>(
        `/api/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
        { method: "DELETE" },
      ),
    renameEntry: (workspaceId: number, path: string, newPath: string) =>
      request<WorkspaceFileNode>(`/api/workspaces/${workspaceId}/rename`, {
        method: "POST",
        body: JSON.stringify({ path, new_path: newPath }),
      }),
    gitStatus: (workspaceId: number) =>
      request<GitStatus>(`/api/workspaces/${workspaceId}/git/status`),
    gitPull: (workspaceId: number) =>
      request<GitActionResult>(`/api/workspaces/${workspaceId}/git/pull`, {
        method: "POST",
      }),
    gitCommitPush: (
      workspaceId: number,
      message: string,
      paths?: string[],
    ) =>
      request<GitActionResult>(`/api/workspaces/${workspaceId}/git/commit-push`, {
        method: "POST",
        body: JSON.stringify(paths ? { message, paths } : { message }),
      }),
    gitDiscard: (workspaceId: number, path: string) =>
      request<GitStatus>(
        `/api/workspaces/${workspaceId}/git/discard?path=${encodeURIComponent(path)}`,
        { method: "POST" },
      ),
    gitDiff: (workspaceId: number, path: string) =>
      request<FileDiff>(
        `/api/workspaces/${workspaceId}/git/diff?path=${encodeURIComponent(path)}`,
      ),
    localPath: (workspaceId: number) =>
      request<LocalPath>(`/api/workspaces/${workspaceId}/local-path`),
  },

  drawio: {
    // Self-hosted diagrams.net webapp, downloaded on demand into the data dir.
    status: () => request<DrawioStatus>(`/api/drawio/status`),
    install: () =>
      request<DrawioStatus>(`/api/drawio/install`, { method: "POST" }),
  },

  system: {
    // Version
    getVersion: () => request<AppVersion>(`/api/version`),

    // Usage statistics
    getUsageStats: () => request<UsageStats>(`/api/stats/usage`),
    getSystemStats: () => request<SystemStats>(`/api/stats/system`),

    // Storage cockpit: what each retention sweep would free, running one on
    // demand, and returning freed pages to the filesystem.
    getCleanupPreview: () => request<CleanupPreview>(`/api/stats/cleanup`),
    runCleanup: (key: string) =>
      request<CleanupRunResult>(`/api/stats/cleanup/${encodeURIComponent(key)}`, {
        method: "POST",
      }),
    compactDatabase: () =>
      request<CompactResult>(`/api/stats/compact`, { method: "POST" }),
  },

  search: {
    // Cross-entity content search backing the ⌘K palette.
    query: (q: string, limit = 40) =>
      request<SearchResponse>(
        `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      ),
  },

  ai: {
    // Rewrite a block of user-authored text. Backs the "Refine with AI"
    // affordance on textareas across the app.
    refine: (data: RefineRequest) =>
      request<RefineResponse>(`/api/refine`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },
};

/** URL that serves a workspace file's raw bytes (static-web-server style). */
export function workspaceRawUrl(slug: string, path: string): string {
  const encoded = path
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return `/raw/${encodeURIComponent(slug)}/${encoded}`;
}
