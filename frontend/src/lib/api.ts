import type {
  AgendaEvent,
  AgentApprovalPolicy,
  AgentArtifactCreate,
  AgentBlueprintCreate,
  AgentBlueprintInstantiate,
  AgentBlueprintUpdate,
  AgentLink,
  AgentPermissionDecisionValue,
  AgentRewindMode,
  AgentScheduleCreate,
  AgentScheduleUpdate,
  AgentSession,
  AgentSessionCreate,
  AgentTriggerCreate,
  ApprovalDecisionResult,
  ApprovalRuleCreate,
  ApprovalRuleUpdate,
  Attachment,
  BackupRunResult,
  CatalogPlugin,
  ChatCreate,
  ChatUpdate,
  CleanupPreview,
  CollectionCreate,
  CollectionUpdate,
  CommentDraft,
  ContextEstimate,
  ConversationArtifact,
  DefinitionFileKind,
  DefinitionKind,
  GitHubIssue,
  IQSection,
  InstalledPlugin,
  IssueLabel,
  MCPServerCreate,
  MCPServerStatus,
  MCPServerUpdate,
  McpAuthDiagnostics,
  MeetingSegmentCreate,
  MeetingSegmentUpdate,
  MeetingSession,
  MeetingSessionCreate,
  MeetingSessionUpdate,
  MeetingSummaryOptions,
  MemoryCreate,
  MemoryUpdate,
  Message,
  MigrationKind,
  MigrationPreview,
  NoteDraftAttachment,
  NotesDraft,
  PluginDescriptor,
  PluginEnvironment,
  PluginInstallResult,
  PluginUpdate,
  PluginVersions,
  RefineRequest,
  Reminder,
  ReminderContainer,
  ReminderCreate,
  RewindResult,
  RoleCreate,
  RoleUpdate,
  ScheduleUpdate,
  SettableStepStatus,
  Settings,
  SettingsUpdate,
  SkillCreate,
  SkillUpdate,
  StepCreate,
  StepMove,
  StepPlanAmendRequest,
  StepPlanCreate,
  StepPlanUpdate,
  StepUpdate,
  StoppedTurn,
  ToolApproval,
  ToolApprovalDecision,
  Topic,
  TopicNode,
  TopicScheduleCreate,
  TopicSummaryResolve,
  TransferResolution,
  TurnIndexItem,
  WorkflowCreate,
  WorkflowScheduleUpdate,
  WorkflowStepRejectPolicy,
  WorkflowUpdate,
  Workspace,
  WorkspaceCreate,
} from "./types";
import type * as Api from "./generated/apiTypes";
import { CLIENT_ID } from "./clientId";
import { call, httpError, untyped } from "./apiClient";

export { apiErrorMessage, call, httpError, request } from "./apiClient";

/** Options for windowed (cursor-paginated) message listing. */
export interface MessageWindow {
  /** Max rows to return — the server caps this. Omit for the full transcript. */
  limit?: number;
  /** Return rows older than this message id (the oldest one already loaded). */
  beforeId?: number;
}

function messageWindow(opts?: MessageWindow) {
  return { query: { limit: opts?.limit, before_id: opts?.beforeId } };
}

/**
 * The endpoints topics and flat chats both expose, under different URLs. Shared
 * conversation code goes through `api.container(kind, id)` instead of branching
 * on the kind at every call site.
 */
export interface ContainerApi {
  listMessages: (opts?: MessageWindow) => Promise<Message[]>;
  deleteMessage: (messageId: number) => Promise<void>;
  /** Edit an assistant reply; sending its original text restores it. */
  editMessage: (messageId: number, content: string) => Promise<Message>;
  /** Erase the whole transcript server-side. */
  clearMessages: () => Promise<void>;
  /**
   * Persist what a user-stopped turn leaves behind: the partial reply and the
   * tool calls still running. Returns the rows created, oldest first.
   */
  saveStopped: (turn: StoppedTurn) => Promise<Message[]>;
  /**
   * Stop the turn generating server-side. A turn outlives its stream (a reload
   * must not lose it), so aborting the request alone no longer stops it.
   */
  stop: () => Promise<void>;
  /**
   * Summarise the conversation so far into a compaction marker; the model sees
   * the summary instead of older turns. Deleting the marker undoes it.
   */
  compact: (instructions?: string) => Promise<Message>;
  /** Estimated history the next turn sends to the model. */
  contextEstimate: () => Promise<ContextEstimate>;
  /** One entry per prompt, oldest first — the transcript timeline's index. */
  listTurns: () => Promise<TurnIndexItem[]>;
  /**
   * Delete the turn opened by `fromMessageId` (a user prompt) and everything
   * after it, up to `throughMessageId` when given.
   */
  rewind: (fromMessageId: number, throughMessageId?: number) => Promise<RewindResult>;
  uploadAttachment: (file: File) => Promise<Attachment>;
  /** Tool calls waiting on the user, plus decided ones not yet run. */
  listApprovals: () => Promise<ToolApproval[]>;
  decideApproval: (approvalId: number, decision: ToolApprovalDecision) => Promise<ApprovalDecisionResult>;
  notes: {
    getDraft: () => Promise<NotesDraft>;
    saveDraft: (text: string) => Promise<NotesDraft>;
    clearDraft: () => Promise<void>;
    append: (text: string, attachmentIds: number[]) => Promise<{ message: Message }>;
    rephrase: (text: string) => Promise<{ text: string }>;
    uploadAttachment: (file: File) => Promise<NoteDraftAttachment>;
    deleteAttachment: (attachmentId: number) => Promise<void>;
  };
}

function rewindBody(fromMessageId: number, throughMessageId?: number) {
  return { from_message_id: fromMessageId, through_message_id: throughMessageId ?? null };
}

function compactBody(instructions?: string) {
  return { instructions: instructions?.trim() || null };
}

export const api = {
  topics: {
    // Topics
    list: (q?: string, collectionId?: number | null) =>
      call("GET /api/topics", {
        query: { q: q || undefined, collection_id: collectionId },
      }) as Promise<Topic[]>,
    tree: (collectionId?: number | null) =>
      call("GET /api/topics/tree", {
        query: { collection_id: collectionId },
      }) as Promise<TopicNode[]>,
    get: (id: number) =>
      call("GET /api/topics/{topic_id}", { path: { topic_id: id } }) as Promise<Topic>,
    getBySlug: (slug: string) =>
      call("GET /api/topics/by-slug/{slug}", { path: { slug } }) as Promise<Topic>,
    // Resolve the immutable `/topics/id/<uuid>` permalink (also legacy `/t/`).
    getByPublicId: (publicId: string) =>
      call("GET /api/topics/by-public-id/{public_id}", {
        path: { public_id: publicId },
      }) as Promise<Topic>,
    create: (data: Api.TopicCreate) => call("POST /api/topics", { body: data }) as Promise<Topic>,
    update: (id: number, data: Partial<Topic>) =>
      call("PATCH /api/topics/{topic_id}", {
        path: { topic_id: id },
        body: data,
      }) as Promise<Topic>,
    // /suggest-name — re-derive the topic's title from its transcript. Renames
    // the topic server-side and returns the new title ("" when unusable).
    suggestName: (id: number) =>
      call("POST /api/topics/{topic_id}/commands/suggest-name", { path: { topic_id: id } }),
    remove: (id: number) => call("DELETE /api/topics/{topic_id}", { path: { topic_id: id } }),
    markRead: (id: number) => call("POST /api/topics/{topic_id}/read", { path: { topic_id: id } }),
    markUnread: (id: number) =>
      call("POST /api/topics/{topic_id}/unread", { path: { topic_id: id } }),
    listArchived: (collectionId?: number | null) =>
      call("GET /api/topics/archived", {
        query: { collection_id: collectionId },
      }) as Promise<Topic[]>,
    // `sideChatsToo` archives the topic's open side chats with it; restoring
    // the topic brings those back.
    archive: (id: number, opts?: { sideChatsToo?: boolean }) =>
      call("POST /api/topics/{topic_id}/archive", {
        path: { topic_id: id },
        query: { side_chats_too: opts?.sideChatsToo || undefined },
      }) as Promise<Topic>,
    unarchive: (id: number) =>
      call("POST /api/topics/{topic_id}/unarchive", { path: { topic_id: id } }) as Promise<Topic>,

    // Topic schedules (run a topic's prompt on a recurrence). Keyed by topic id;
    // mirror the agent schedule endpoints.
    getSchedule: (topicId: number) =>
      call("GET /api/topics/{topic_id}/schedule", { path: { topic_id: topicId } }),
    createSchedule: (topicId: number, data: TopicScheduleCreate) =>
      call("POST /api/topics/{topic_id}/schedule", { path: { topic_id: topicId }, body: data }),
    updateSchedule: (topicId: number, data: ScheduleUpdate) =>
      call("PATCH /api/topics/{topic_id}/schedule", { path: { topic_id: topicId }, body: data }),
    deleteSchedule: (topicId: number) =>
      call("DELETE /api/topics/{topic_id}/schedule", { path: { topic_id: topicId } }),
    runScheduleNow: (topicId: number) =>
      call("POST /api/topics/{topic_id}/schedule/run", { path: { topic_id: topicId } }),
  },

  sideChats: {
    // Chats started from a topic, or from one of its assistant replies.
    list: (topicId: number) =>
      call("GET /api/topics/{topic_id}/chats", { path: { topic_id: topicId } }),
    // `quote` narrows the copied reply to an excerpt of it (a text selection).
    create: (topicId: number, messageId?: number | null, quote?: string | null) =>
      call("POST /api/topics/{topic_id}/chats", {
        path: { topic_id: topicId },
        body: { message_id: messageId ?? null, quote: quote ?? null },
      }),
    // Delete a side chat left untouched; rejects (409) when it was used.
    discard: (chatId: number) =>
      call("POST /api/chats/{chat_id}/discard", { path: { chat_id: chatId } }),
    // Have the model sum up a side chat for its parent topic (nothing saved).
    draftTopicNote: (chatId: number, instructions?: string) =>
      call("POST /api/chats/{chat_id}/topic-note/draft", {
        path: { chat_id: chatId },
        body: { instructions: instructions || null },
      }),
    sendTopicNote: (chatId: number, text: string) =>
      call("POST /api/chats/{chat_id}/topic-note", { path: { chat_id: chatId }, body: { text } }),
  },
  chats: {
    // Chats (flat conversation sessions — no tree, no GitHub link)
    list: (q?: string) => call("GET /api/chats", { query: { q: q || undefined } }),
    listArchived: () => call("GET /api/chats/archived"),
    get: (id: number) => call("GET /api/chats/{chat_id}", { path: { chat_id: id } }),
    getBySlug: (slug: string) => call("GET /api/chats/by-slug/{slug}", { path: { slug } }),
    getByPublicId: (publicId: string) =>
      call("GET /api/chats/by-public-id/{public_id}", { path: { public_id: publicId } }),
    create: (data: ChatCreate) => call("POST /api/chats", { body: data }),
    update: (id: number, data: ChatUpdate) =>
      call("PATCH /api/chats/{chat_id}", { path: { chat_id: id }, body: data }),
    remove: (id: number) => call("DELETE /api/chats/{chat_id}", { path: { chat_id: id } }),
    markRead: (id: number) => call("POST /api/chats/{chat_id}/read", { path: { chat_id: id } }),
    markUnread: (id: number) => call("POST /api/chats/{chat_id}/unread", { path: { chat_id: id } }),
    archive: (id: number) => call("POST /api/chats/{chat_id}/archive", { path: { chat_id: id } }),
    unarchive: (id: number) =>
      call("POST /api/chats/{chat_id}/unarchive", { path: { chat_id: id } }),
    // Promote a flat chat into a full topic (moves the transcript over). Chats
    // have no collection, so the caller names the one it is looking at.
    promote: (id: number, collectionId?: number | null, parentId?: number | null) =>
      call("POST /api/chats/{chat_id}/promote", {
        path: { chat_id: id },
        query: { collection_id: collectionId, parent_id: parentId },
      }) as Promise<Topic>,

    // Chat messages (mirror topic message endpoints)
    listMessages: (chatId: number, opts?: MessageWindow) =>
      call("GET /api/chats/{chat_id}/messages", {
        path: { chat_id: chatId },
        ...messageWindow(opts),
      }),
    clearMessages: (chatId: number) =>
      call("DELETE /api/chats/{chat_id}/messages", { path: { chat_id: chatId } }),
    deleteMessage: (chatId: number, messageId: number) =>
      call("DELETE /api/chats/{chat_id}/messages/{message_id}", {
        path: { chat_id: chatId, message_id: messageId },
      }),
    editMessage: (chatId: number, messageId: number, content: string) =>
      call("PATCH /api/chats/{chat_id}/messages/{message_id}", {
        path: { chat_id: chatId, message_id: messageId },
        body: { content },
      }),
    saveStoppedMessage: (chatId: number, turn: StoppedTurn) =>
      call("POST /api/chats/{chat_id}/messages/stopped", { path: { chat_id: chatId }, body: turn }),
    stopTurn: (chatId: number) =>
      call("POST /api/chats/{chat_id}/messages/stop", { path: { chat_id: chatId } }),
    // /suggest-name — re-derive the chat's title from its transcript.
    suggestName: (chatId: number) =>
      call("POST /api/chats/{chat_id}/messages/suggest-name", { path: { chat_id: chatId } }),
    // /notes for chats (no GitHub comment option)
    rephraseNotes: (chatId: number, text: string, instruction?: string) =>
      call("POST /api/chats/{chat_id}/messages/notes/rephrase", {
        path: { chat_id: chatId },
        body: { text, instruction: instruction ?? null },
      }),
    appendNotes: (chatId: number, text: string, attachmentIds: number[] = []) =>
      call("POST /api/chats/{chat_id}/messages/notes/append", {
        path: { chat_id: chatId },
        body: { text, attachment_ids: attachmentIds },
      }),
    getNotesDraft: (chatId: number) =>
      call("GET /api/chats/{chat_id}/messages/notes/draft", { path: { chat_id: chatId } }),
    saveNotesDraft: (chatId: number, text: string) =>
      call("PUT /api/chats/{chat_id}/messages/notes/draft", {
        path: { chat_id: chatId },
        body: { text },
      }),
    clearNotesDraft: (chatId: number) =>
      call("DELETE /api/chats/{chat_id}/messages/notes/draft", { path: { chat_id: chatId } }),
    listNoteAttachments: (chatId: number) =>
      call("GET /api/chats/{chat_id}/messages/notes/attachments", { path: { chat_id: chatId } }),
    uploadNoteAttachment: (chatId: number, file: File) =>
      call("POST /api/chats/{chat_id}/messages/notes/attachments", {
        path: { chat_id: chatId },
        form: { file },
      }),
    deleteNoteAttachment: (chatId: number, attachmentId: number) =>
      call("DELETE /api/chats/{chat_id}/messages/notes/attachments/{attachment_id}", {
        path: { chat_id: chatId, attachment_id: attachmentId },
      }),
  },

  reminders: {
    // Reminders (one-shot date/time). Keyed by container kind + id; shared by
    // topics and chats. listReminders returns only fired (awaiting acknowledgment).
    list: () => call("GET /api/reminders"),
    // Resolves to null when the conversation has no reminder (a 200, not a 404).
    get: (container: ReminderContainer, id: number) =>
      call("GET /api/reminders/{container}/{container_id}", {
        path: { container, container_id: id },
      }) as Promise<Reminder | null>,
    set: (container: ReminderContainer, id: number, data: ReminderCreate) =>
      call("PUT /api/reminders/{container}/{container_id}", {
        path: { container, container_id: id },
        body: data,
      }) as Promise<Reminder>,
    // Used for both /reminder-cancel (pending) and /done (fired) — both delete.
    clear: (container: ReminderContainer, id: number) =>
      call("DELETE /api/reminders/{container}/{container_id}", {
        path: { container, container_id: id },
      }),
  },

  agents: {
    // Agents mode (Copilot SDK). Long-running agent sessions, optionally attached
    // to a topic/chat. Live progress arrives via the `agent.changed` SSE event;
    // the step timeline is re-fetched from `/events` on each signal.
    list: (filter?: { topicId?: number; chatId?: number }) =>
      call("GET /api/agents", { query: { topic_id: filter?.topicId, chat_id: filter?.chatId } }),
    get: (id: number | string) =>
      untyped<AgentSession>(call("GET /api/agents/{agent_id}", { path: { agent_id: id } })),
    markRead: (id: number | string) =>
      call("POST /api/agents/{agent_id}/read", { path: { agent_id: id } }),
    markUnread: (id: number | string) =>
      call("POST /api/agents/{agent_id}/unread", { path: { agent_id: id } }),
    create: (data: AgentSessionCreate) => call("POST /api/agents", { body: data }),
    // `agentRunId` narrows the transcript to one execution — without it a
    // reusable agent driven by two workflows at once reads as one conversation.
    // `after` is the cursor from the previous read: the timeline only grows
    // between rewinds, so a live view asks only for the events it hasn't seen.
    // `epoch` from that read resets the cursor when a rewind happened since.
    // Omit both for the whole transcript.
    getEvents: (id: number, agentRunId?: number | null, after = 0, epoch?: string | null) =>
      call("GET /api/agents/{agent_id}/events", {
        path: { agent_id: id },
        query: {
          agent_run_id: agentRunId,
          after: after > 0 ? after : undefined,
          epoch: after > 0 && epoch ? epoch : undefined,
        },
      }),
    // Drop the prompt `eventId` (an `AgentEvent.event_id`) of the current run
    // and everything after it, in the SDK session and the transcript. Irreversible.
    // `conversation-and-files` also restores the files the dropped turns changed.
    rewind: (id: number, eventId: string, mode: AgentRewindMode = "conversation") =>
      call("POST /api/agents/{agent_id}/rewind", {
        path: { agent_id: id },
        body: { event_id: eventId, mode },
      }),
    // Checks a rewind to `eventId` and lists the files it could restore. Changes nothing.
    previewRewind: (id: number, eventId: string) =>
      call("GET /api/agents/{agent_id}/rewind/preview", {
        path: { agent_id: id },
        query: { event_id: eventId },
      }),
    listModels: () => call("GET /api/agents/models"),
    // Runtime capability + provisioning. Unlike the rest of this namespace these
    // stay reachable when the runtime is down — they are how it gets fixed.
    getRuntime: () => call("GET /api/agents/runtime"),
    installCli: () => call("POST /api/agents/runtime/cli"),
    restartForRuntime: () => call("POST /api/agents/runtime/restart") as Promise<void>,
    listPermissions: () => call("GET /api/agents/permissions"),
    resetPermissions: () =>
      call("POST /api/agents/permissions/reset") as Promise<{ cleared: number }>,
    send: (id: number | string, message: string) =>
      call("POST /api/agents/{agent_id}/send", { path: { agent_id: id }, body: { message } }),
    cancel: (id: number) =>
      untyped<AgentSession>(call("POST /api/agents/{agent_id}/cancel", { path: { agent_id: id } })),
    start: (id: number | string) =>
      call("POST /api/agents/{agent_id}/start", { path: { agent_id: id } }),
    resume: (id: number | string) =>
      call("POST /api/agents/{agent_id}/resume", { path: { agent_id: id } }),
    resolvePermission: (
      id: number,
      requestId: string,
      decision: AgentPermissionDecisionValue,
    ) =>
      call("POST /api/agents/{agent_id}/permission", {
        path: { agent_id: id },
        body: { request_id: requestId, decision },
      }),
    link: (id: number, link: AgentLink) =>
      call("PATCH /api/agents/{agent_id}/link", {
        path: { agent_id: id },
        body: { topic_id: link.topic_id ?? null, chat_id: link.chat_id ?? null },
      }),
    rename: (id: number, title: string) =>
      call("PATCH /api/agents/{agent_id}", { path: { agent_id: id }, body: { title } }),
    update: (
      id: number,
      payload: {
        title?: string;
        task?: string;
        role?: string | null;
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
    ) => call("PATCH /api/agents/{agent_id}", { path: { agent_id: id }, body: payload }),
    remove: (id: number) => call("DELETE /api/agents/{agent_id}", { path: { agent_id: id } }),
    listArchived: () => call("GET /api/agents/archived"),
    /** Workflows that reference this agent (archived ones excluded). */
    workflows: (id: number) =>
      call("GET /api/agents/{agent_id}/workflows", { path: { agent_id: id } }),
    /**
     * This agent's execution history, newest first. An agent is a reusable
     * definition, so "what did it do, driven by what, and what did that cost"
     * is a per-run question.
     */
    runs: (id: number | string, opts?: { workflowRunId?: number; limit?: number }) =>
      call("GET /api/agents/{agent_id}/runs", {
        path: { agent_id: id },
        query: { workflow_run_id: opts?.workflowRunId, limit: opts?.limit },
      }),
    run: (id: number | string, runId: number) =>
      call("GET /api/agents/{agent_id}/runs/{run_id}", { path: { agent_id: id, run_id: runId } }),
    archive: (id: number) =>
      call("POST /api/agents/{agent_id}/archive", { path: { agent_id: id } }),
    unarchive: (id: number) =>
      call("POST /api/agents/{agent_id}/unarchive", { path: { agent_id: id } }),

    // Agent schedules (recurring auto-re-run of an agent's task). Keyed by the
    // agent's id or public uuid; mirror the topic schedule endpoints.
    getSchedule: (id: number | string) =>
      call("GET /api/agents/{agent_id}/schedule", { path: { agent_id: id } }),
    createSchedule: (id: number | string, data: AgentScheduleCreate) =>
      call("POST /api/agents/{agent_id}/schedule", { path: { agent_id: id }, body: data }),
    updateSchedule: (id: number | string, data: AgentScheduleUpdate) =>
      call("PATCH /api/agents/{agent_id}/schedule", { path: { agent_id: id }, body: data }),
    deleteSchedule: (id: number | string) =>
      call("DELETE /api/agents/{agent_id}/schedule", { path: { agent_id: id } }),
    runScheduleNow: (id: number | string) =>
      call("POST /api/agents/{agent_id}/schedule/run", { path: { agent_id: id } }),

    // --- Orchestrator: aggregate observability + unified inbox -------------
    // Fleet-wide rollup (status counts, token totals, concurrency headroom).
    metrics: () => call("GET /api/agents/metrics"),
    // Everything waiting on a human: raised questions, permission gates, budget parks.
    inbox: () => call("GET /api/agents/inbox"),

    // --- Blueprints (reusable agent templates) ----------------------------
    listBlueprints: () => call("GET /api/agents/blueprints"),
    createBlueprint: (data: AgentBlueprintCreate) =>
      call("POST /api/agents/blueprints", { body: data }),
    updateBlueprint: (id: number, data: AgentBlueprintUpdate) =>
      call("PATCH /api/agents/blueprints/{blueprint_id}", {
        path: { blueprint_id: id },
        body: data,
      }),
    deleteBlueprint: (id: number) =>
      call("DELETE /api/agents/blueprints/{blueprint_id}", { path: { blueprint_id: id } }),
    instantiateBlueprint: (id: number, data: AgentBlueprintInstantiate) =>
      call("POST /api/agents/blueprints/{blueprint_id}/instantiate", {
        path: { blueprint_id: id },
        body: data,
      }),

    // --- Shared artifacts / blackboard ------------------------------------
    listArtifacts: (id: number) =>
      call("GET /api/agents/{agent_id}/artifacts", { path: { agent_id: id } }),
    getArtifact: (id: number, artifactId: number) =>
      call("GET /api/agents/{agent_id}/artifacts/{artifact_id}", {
        path: { agent_id: id, artifact_id: artifactId },
      }),
    // Browser-openable URL for an artifact's raw body (kind-appropriate
    // content-type; a `link` artifact redirects to its URL). Used for the
    // "Open raw" affordance and programmatic/download access.
    rawArtifactUrl: (id: number, artifactId: number) =>
      `/api/agents/${id}/artifacts/${artifactId}/raw`,
    createArtifact: (id: number, data: AgentArtifactCreate) =>
      call("POST /api/agents/{agent_id}/artifacts", { path: { agent_id: id }, body: data }),

    // --- State (private cross-run scratchpad) -----------------------------
    // Distinct from artifacts: state is private bookkeeping that *survives*
    // re-runs, so this surface is mostly for inspecting (or resetting) a
    // recurring agent's saved cursor.
    listState: (id: number) => call("GET /api/agents/{agent_id}/state", { path: { agent_id: id } }),
    setState: (id: number, key: string, value: string) =>
      call("PUT /api/agents/{agent_id}/state", { path: { agent_id: id }, body: { key, value } }),
    deleteState: (id: number, key: string) =>
      call("DELETE /api/agents/{agent_id}/state/{key}", { path: { agent_id: id, key } }),
    clearState: (id: number) =>
      call("DELETE /api/agents/{agent_id}/state", { path: { agent_id: id } }),

    // --- Triggers (external webhooks) -------------------------------------
    listTriggers: (id: number) =>
      call("GET /api/agents/{agent_id}/triggers", { path: { agent_id: id } }),
    createTrigger: (id: number, data: AgentTriggerCreate = {}) =>
      call("POST /api/agents/{agent_id}/triggers", { path: { agent_id: id }, body: data }),
    deleteTrigger: (id: number, triggerId: number) =>
      call("DELETE /api/agents/{agent_id}/triggers/{trigger_id}", {
        path: { agent_id: id, trigger_id: triggerId },
      }),
  },

  stepPlans: {
    // Step by step: a topic's long ask split into an editable tree of steps, each
    // with a versioned Markdown artifact and its own chat, then consolidated into
    // one final output. Model-backed calls (create with propose, propose,
    // suggest-*, generate) are slow and answer 502 when the model fails.
    list: (topicId?: number, chatId?: number) =>
      call("GET /api/step-plans", {
        query: topicId != null ? { topic_id: topicId } : { chat_id: chatId },
      }),
    listArchived: () => call("GET /api/step-plans/archived"),
    create: (payload: StepPlanCreate) => call("POST /api/step-plans", { body: payload }),
    get: (id: number, signal?: AbortSignal) =>
      call("GET /api/step-plans/{plan_id}", { path: { plan_id: id }, signal }),
    update: (id: number, payload: StepPlanUpdate) =>
      call("PATCH /api/step-plans/{plan_id}", { path: { plan_id: id }, body: payload }),
    amend: (id: number, payload: StepPlanAmendRequest) =>
      call("POST /api/step-plans/{plan_id}/amend", { path: { plan_id: id }, body: payload }),
    archive: (id: number) =>
      call("POST /api/step-plans/{plan_id}/archive", { path: { plan_id: id } }),
    unarchive: (id: number) =>
      call("POST /api/step-plans/{plan_id}/unarchive", { path: { plan_id: id } }),
    remove: (id: number) => call("DELETE /api/step-plans/{plan_id}", { path: { plan_id: id } }),
    propose: (id: number, instructions?: string) =>
      call("POST /api/step-plans/{plan_id}/propose", {
        path: { plan_id: id },
        body: { instructions: instructions || null },
      }),
    suggestMissing: (id: number, instructions?: string) =>
      call("POST /api/step-plans/{plan_id}/suggest-missing", {
        path: { plan_id: id },
        body: { instructions: instructions || null },
      }),
    start: (id: number, acceptProposed = true) =>
      call("POST /api/step-plans/{plan_id}/start", {
        path: { plan_id: id },
        body: { accept_proposed: acceptProposed },
      }),
    startAuto: (id: number) =>
      call("POST /api/step-plans/{plan_id}/auto/start", { path: { plan_id: id } }),
    launch: (id: number) =>
      call("POST /api/step-plans/{plan_id}/launch", { path: { plan_id: id } }),
    pauseAuto: (id: number) =>
      call("POST /api/step-plans/{plan_id}/auto/pause", { path: { plan_id: id } }),
    assessPlan: (id: number) =>
      call("POST /api/step-plans/{plan_id}/assess", { path: { plan_id: id } }),
    assessStep: (stepId: number) =>
      call("POST /api/steps/{step_id}/assess", { path: { step_id: stepId } }),
    review: (id: number) =>
      call("POST /api/step-plans/{plan_id}/review", { path: { plan_id: id } }),
    createStep: (id: number, payload: StepCreate) =>
      call("POST /api/step-plans/{plan_id}/steps", { path: { plan_id: id }, body: payload }),
    consolidationChat: (id: number) =>
      call("POST /api/step-plans/{plan_id}/consolidation/chat", { path: { plan_id: id } }),
    generateFinal: (id: number, opts?: { instructions?: string; force?: boolean }) =>
      call("POST /api/step-plans/{plan_id}/consolidate/generate", {
        path: { plan_id: id },
        body: { instructions: opts?.instructions || null, force: opts?.force ?? false, },
      }),
    writeFinal: (id: number, content: string) =>
      call("PUT /api/step-plans/{plan_id}/final", { path: { plan_id: id }, body: { content } }),
    finalVersions: (id: number) =>
      call("GET /api/step-plans/{plan_id}/final/versions", { path: { plan_id: id } }),
    revertFinal: (id: number, version: number) =>
      call("POST /api/step-plans/{plan_id}/final/revert", {
        path: { plan_id: id },
        body: { version },
      }),
    finalFromMessage: (id: number, messageId: number) =>
      call("POST /api/step-plans/{plan_id}/final/from-message", {
        path: { plan_id: id },
        body: { message_id: messageId },
      }),
    complete: (id: number) =>
      call("POST /api/step-plans/{plan_id}/complete", { path: { plan_id: id } }),
    sendToTopic: (id: number, text?: string, force = false) =>
      call("POST /api/step-plans/{plan_id}/send-to-topic", {
        path: { plan_id: id },
        body: { text: text ?? null, force },
      }),
    sendToSource: (id: number, text?: string, force = false) =>
      call("POST /api/step-plans/{plan_id}/send-to-source", {
        path: { plan_id: id },
        body: { text: text ?? null, force },
      }),
    updateStep: (stepId: number, payload: StepUpdate) =>
      call("PATCH /api/steps/{step_id}", { path: { step_id: stepId }, body: payload }),
    removeStep: (stepId: number) =>
      call("DELETE /api/steps/{step_id}", { path: { step_id: stepId } }),
    moveStep: (stepId: number, payload: StepMove) =>
      call("POST /api/steps/{step_id}/move", { path: { step_id: stepId }, body: payload }),
    acceptStep: (stepId: number) =>
      call("POST /api/steps/{step_id}/accept", { path: { step_id: stepId } }),
    setStepStatus: (stepId: number, status: SettableStepStatus) =>
      call("POST /api/steps/{step_id}/status", { path: { step_id: stepId }, body: { status } }),
    writeArtifact: (stepId: number, content: string) =>
      call("PUT /api/steps/{step_id}/artifact", { path: { step_id: stepId }, body: { content } }),
    versions: (stepId: number) =>
      call("GET /api/steps/{step_id}/versions", { path: { step_id: stepId } }),
    revertArtifact: (stepId: number, version: number) =>
      call("POST /api/steps/{step_id}/artifact/revert", {
        path: { step_id: stepId },
        body: { version },
      }),
    generateArtifact: (stepId: number, instructions?: string) =>
      call("POST /api/steps/{step_id}/artifact/generate", {
        path: { step_id: stepId },
        body: { instructions: instructions || null },
      }),
    artifactFromMessage: (stepId: number, messageId: number) =>
      call("POST /api/steps/{step_id}/artifact/from-message", {
        path: { step_id: stepId },
        body: { message_id: messageId },
      }),
    suggestSubsteps: (stepId: number, instructions?: string) =>
      call("POST /api/steps/{step_id}/suggest-substeps", {
        path: { step_id: stepId },
        body: { instructions: instructions || null },
      }),
    stepChat: (stepId: number) =>
      call("POST /api/steps/{step_id}/chat", { path: { step_id: stepId } }),
  },

  workflows: {
    // Workflows: reusable, named sequences of independent agents. The *workflow*
    // owns the chaining (not agent-to-agent dependencies). Live progress arrives
    // via the `workflow.changed` SSE event.
    list: (opts?: { includeArchived?: boolean }) =>
      call("GET /api/workflows", {
        query: { include_archived: opts?.includeArchived || undefined },
      }),
    get: (id: number) => call("GET /api/workflows/{workflow_id}", { path: { workflow_id: id } }),
    // Durable run history (newest first), each with its per-step attempt traces.
    runs: (id: number, limit?: number) =>
      call("GET /api/workflows/{workflow_id}/runs", {
        path: { workflow_id: id },
        query: { limit: limit || undefined },
      }),
    // The agent's activity for one step *attempt* — tool calls, reasoning,
    // errors. Sliced to the attempt's window, so an agent re-driven several
    // times doesn't replay its whole history under every trace row.
    stepEvents: (id: number, stepRunId: number) =>
      call("GET /api/workflows/{workflow_id}/run-steps/{step_run_id}/events", {
        path: { workflow_id: id, step_run_id: stepRunId },
      }),
    create: (data: WorkflowCreate) => call("POST /api/workflows", { body: data }),
    update: (id: number, data: WorkflowUpdate) =>
      call("PATCH /api/workflows/{workflow_id}", { path: { workflow_id: id }, body: data }),
    remove: (id: number) =>
      call("DELETE /api/workflows/{workflow_id}", { path: { workflow_id: id } }),
    // Lifecycle controls — the workflow coordinates its step agents.
    run: (id: number, input?: string | null) =>
      call("POST /api/workflows/{workflow_id}/run", {
        path: { workflow_id: id },
        body: { input: input?.trim() ? input.trim() : null },
      }),
    approve: (id: number, note?: string | null) =>
      call("POST /api/workflows/{workflow_id}/approve", {
        path: { workflow_id: id },
        body: { note: note?.trim() ? note.trim() : null },
      }),
    reject: (id: number, note?: string | null, action?: WorkflowStepRejectPolicy | null) =>
      call("POST /api/workflows/{workflow_id}/reject", {
        path: { workflow_id: id },
        body: { note: note?.trim() ? note.trim() : null, action: action ?? null, },
      }),
    pause: (id: number) =>
      call("POST /api/workflows/{workflow_id}/pause", { path: { workflow_id: id } }),
    // Resume a paused run. `input` answers whatever parked it — when the pause
    // came from a step's agent blocking on a question, re-driving it blind would
    // just hit the same question again.
    resume: (id: number, input?: string | null) =>
      call("POST /api/workflows/{workflow_id}/resume", {
        path: { workflow_id: id },
        body: { input: input?.trim() ? input.trim() : null },
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
      call("POST /api/workflows/{workflow_id}/permission", {
        path: { workflow_id: id },
        body: { request_id: requestId, decision },
      }),
    // Re-drive one step of a stopped run as a fresh attempt, in place — instead
    // of re-running the whole pipeline and paying for the good steps twice.
    // Omit `position` to retry the step whose failure stopped the run.
    retry: (id: number, opts?: { position?: number | null; input?: string | null }) =>
      call("POST /api/workflows/{workflow_id}/retry", {
        path: { workflow_id: id },
        body: {
          position: opts?.position ?? null,
          input: opts?.input?.trim() ? opts.input.trim() : null,
        },
      }),
    cancel: (id: number) =>
      call("POST /api/workflows/{workflow_id}/cancel", { path: { workflow_id: id } }),
    // Run one recorded step attempt again, alone, on the input it first saw.
    // Unlike `retry` this advances nothing — so it works on a run that
    // succeeded, when you just want another take on one step.
    replayStep: (id: number, stepRunId: number) =>
      call("POST /api/workflows/{workflow_id}/run-steps/{step_run_id}/replay", {
        path: { workflow_id: id, step_run_id: stepRunId },
      }),
    listArchived: () => call("GET /api/workflows/archived"),
    archive: (id: number) =>
      call("POST /api/workflows/{workflow_id}/archive", { path: { workflow_id: id } }),
    unarchive: (id: number) =>
      call("POST /api/workflows/{workflow_id}/unarchive", { path: { workflow_id: id } }),
    // Recurrence config (PUT replaces the whole schedule block).
    setSchedule: (id: number, data: WorkflowScheduleUpdate) =>
      call("PUT /api/workflows/{workflow_id}/schedule", { path: { workflow_id: id }, body: data }),
    // Webhook trigger: mint returns the workflow with `webhook_token` populated.
    mintWebhook: (id: number) =>
      call("POST /api/workflows/{workflow_id}/webhook", { path: { workflow_id: id } }),
    revokeWebhook: (id: number) =>
      call("DELETE /api/workflows/{workflow_id}/webhook", { path: { workflow_id: id } }),
    // Public webhook URL to copy (fires the workflow when POSTed to).
    webhookUrl: (token: string) => `/api/workflows/hooks/${token}`,

    // --- State (the pipeline's own memory) --------------------------------
    // Named values shared by every step and kept across runs. Setting one by
    // hand is how you seed a pipeline's first run (e.g. its starting cursor).
    listState: (id: number) =>
      call("GET /api/workflows/{workflow_id}/state", { path: { workflow_id: id } }),
    setState: (id: number, key: string, value: string) =>
      call("PUT /api/workflows/{workflow_id}/state", {
        path: { workflow_id: id },
        body: { key, value },
      }),
    deleteState: (id: number, key: string) =>
      call("DELETE /api/workflows/{workflow_id}/state/{key}", { path: { workflow_id: id, key } }),
    clearState: (id: number) =>
      call("DELETE /api/workflows/{workflow_id}/state", { path: { workflow_id: id } }),
  },

  transfer: {
    // YAML export/import. Export is a plain download URL so the browser handles
    // the file save; import is two calls because the replace/create/link choice
    // can only be offered once `preview` has reported the collisions.
    exportWorkflowUrl: (id: number) => `/api/transfer/workflows/${id}`,
    exportAgentUrl: (id: number | string) => `/api/transfer/agents/${id}`,
    preview: (content: string) => call("POST /api/transfer/preview", { body: { content } }),
    import: (content: string, resolutions: TransferResolution[] = []) =>
      call("POST /api/transfer/import", { body: { content, resolutions } }),
  },

  definitions: {
    // Work in progress (docs/definitions.md): the folder check and the one-shot
    // export of database agents/workflows into definition files.
    check: () => call("GET /api/definitions/check"),
    // The migration from database-declared to file-declared agents and workflows.
    status: () => call("GET /api/definitions/status"),
    migration: () => call("GET /api/definitions/migration") as Promise<MigrationPreview>,
    migrate: (acknowledge: boolean) =>
      call("POST /api/definitions/migrate", { body: { acknowledge } }),
    revert: () => call("POST /api/definitions/revert"),
    migrationItem: (kind: MigrationKind, id: number) =>
      call("GET /api/definitions/migration/items/{kind}/{item_id}", {
        path: { kind, item_id: id },
      }),
    // The last, irreversible step: clears the declarations out of the database.
    finalize: () => call("POST /api/definitions/finalize", { body: { confirm: true } }),
    fileIssues: (workspaceId: number, path: string) =>
      call("GET /api/definitions/file-issues", { query: { workspace_id: workspaceId, path } }),
    // The JSON Schema of a kind of definition file, for the Files editor.
    schema: (kind: DefinitionFileKind) =>
      call("GET /api/definitions/schema/{kind}", { path: { kind } }),
    // Files mode: accept the permissions an agent's or workflow's file grants now.
    accept: (kind: DefinitionKind, id: number | string, contentHash?: string | null) =>
      call("POST /api/definitions/accept", {
        body: { kind, id, content_hash: contentHash ?? null },
      }),
    export: (overwrite = false) =>
      call("POST /api/definitions/export", { query: { overwrite: overwrite || undefined } }),
  },

  messages: {
    // Messages
    list: (topicId: number, opts?: MessageWindow) =>
      call("GET /api/topics/{topic_id}/messages", {
        path: { topic_id: topicId },
        ...messageWindow(opts),
      }),
    clear: (topicId: number) =>
      call("DELETE /api/topics/{topic_id}/messages", { path: { topic_id: topicId } }),
    remove: (topicId: number, messageId: number) =>
      call("DELETE /api/topics/{topic_id}/messages/{message_id}", {
        path: { topic_id: topicId, message_id: messageId },
      }),
    edit: (topicId: number, messageId: number, content: string) =>
      call("PATCH /api/topics/{topic_id}/messages/{message_id}", {
        path: { topic_id: topicId, message_id: messageId },
        body: { content },
      }),
    // Persist a partial assistant reply when the user stops generation.
    saveStopped: (topicId: number, turn: StoppedTurn) =>
      call("POST /api/topics/{topic_id}/messages/stopped", {
        path: { topic_id: topicId },
        body: turn,
      }),
    stopTurn: (topicId: number) =>
      call("POST /api/topics/{topic_id}/messages/stop", { path: { topic_id: topicId } }),
  },

  container: (kind: ReminderContainer, id: number): ContainerApi =>
    kind === "topic"
      ? {
          listMessages: (opts) => api.messages.list(id, opts),
          deleteMessage: (mid) => api.messages.remove(id, mid),
          editMessage: (mid, content) => api.messages.edit(id, mid, content),
          clearMessages: () => api.messages.clear(id),
          saveStopped: (turn) => api.messages.saveStopped(id, turn),
          stop: () => api.messages.stopTurn(id),
          compact: (instructions) =>
            call("POST /api/topics/{topic_id}/messages/compact", {
              path: { topic_id: id },
              body: compactBody(instructions),
            }),
          contextEstimate: () =>
            call("GET /api/topics/{topic_id}/messages/context", { path: { topic_id: id } }),
          listTurns: () =>
            call("GET /api/topics/{topic_id}/messages/turns", { path: { topic_id: id } }),
          rewind: (fromId, throughId) =>
            call("POST /api/topics/{topic_id}/messages/rewind", {
              path: { topic_id: id },
              body: rewindBody(fromId, throughId),
            }),
          uploadAttachment: (file) => api.attachments.uploadForTopic(id, file),
          listApprovals: () =>
            call("GET /api/topics/{topic_id}/approvals", { path: { topic_id: id } }),
          decideApproval: (aid, decision) =>
            call("POST /api/topics/{topic_id}/approvals/{approval_id}", {
              path: { topic_id: id, approval_id: aid },
              body: decision,
            }),
          notes: {
            getDraft: () => api.notes.getDraft(id),
            saveDraft: (text) => api.notes.saveDraft(id, text),
            clearDraft: () => api.notes.clearDraft(id),
            append: (text, ids) => api.notes.append(id, text, ids),
            rephrase: (text) => api.notes.rephrase(id, text),
            uploadAttachment: (file) => api.notes.uploadAttachment(id, file),
            deleteAttachment: (attId) => api.notes.deleteAttachment(id, attId),
          },
        }
      : {
          listMessages: (opts) => api.chats.listMessages(id, opts),
          deleteMessage: (mid) => api.chats.deleteMessage(id, mid),
          editMessage: (mid, content) => api.chats.editMessage(id, mid, content),
          clearMessages: () => api.chats.clearMessages(id),
          saveStopped: (turn) => api.chats.saveStoppedMessage(id, turn),
          stop: () => api.chats.stopTurn(id),
          compact: (instructions) =>
            call("POST /api/chats/{chat_id}/messages/compact", {
              path: { chat_id: id },
              body: compactBody(instructions),
            }),
          contextEstimate: () =>
            call("GET /api/chats/{chat_id}/messages/context", { path: { chat_id: id } }),
          listTurns: () =>
            call("GET /api/chats/{chat_id}/messages/turns", { path: { chat_id: id } }),
          rewind: (fromId, throughId) =>
            call("POST /api/chats/{chat_id}/messages/rewind", {
              path: { chat_id: id },
              body: rewindBody(fromId, throughId),
            }),
          uploadAttachment: (file) => api.attachments.uploadForChat(id, file),
          listApprovals: () =>
            call("GET /api/chats/{chat_id}/approvals", { path: { chat_id: id } }),
          decideApproval: (aid, decision) =>
            call("POST /api/chats/{chat_id}/approvals/{approval_id}", {
              path: { chat_id: id, approval_id: aid },
              body: decision,
            }),
          notes: {
            getDraft: () => api.chats.getNotesDraft(id),
            saveDraft: (text) => api.chats.saveNotesDraft(id, text),
            clearDraft: () => api.chats.clearNotesDraft(id),
            append: (text, ids) => api.chats.appendNotes(id, text, ids),
            rephrase: (text) => api.chats.rephraseNotes(id, text),
            uploadAttachment: (file) => api.chats.uploadNoteAttachment(id, file),
            deleteAttachment: (attId) => api.chats.deleteNoteAttachment(id, attId),
          },
        },

  attachments: {
    readText: async (url: string, signal: AbortSignal): Promise<string> => {
      const response = await fetch(url, { signal, headers: { "X-Client-Id": CLIENT_ID } });
      if (!response.ok) throw await httpError(response);
      return response.text();
    },
    // Attachments (images + selected documents)
    uploadForTopic: (topicId: number, file: File) =>
      call("POST /api/topics/{topic_id}/attachments", {
        path: { topic_id: topicId },
        form: { file },
      }),
    uploadForChat: (chatId: number, file: File) =>
      call("POST /api/chats/{chat_id}/attachments", { path: { chat_id: chatId }, form: { file } }),
    remove: (attachmentId: number) =>
      call("DELETE /api/attachments/{attachment_id}", { path: { attachment_id: attachmentId } }),
    url: (attachmentId: number) => `/api/attachments/${attachmentId}`,
  },

  settings: {
    // Settings
    get: () => call("GET /api/settings") as Promise<Settings>,
    update: (data: SettingsUpdate) =>
      call("PUT /api/settings", { body: data }) as Promise<Settings>,
    runBackupNow: () => untyped<BackupRunResult>(call("POST /api/settings/backup/run")),
    regenerateOpenAIProxyKey: () =>
      untyped<{ key: string }>(call("POST /api/settings/openai-proxy/key")),
  },

  stt: {
    // Speech-to-text (Azure token broker)
    getToken: () => call("GET /api/stt/token"),
    testConnection: (endpoint: string, key?: string) =>
      call("POST /api/stt/test", { body: { endpoint, key: key || null } }),
  },

  github: {
    // GitHub
    listIssues: (repo?: string, q?: string) =>
      untyped<GitHubIssue[]>(
        call("GET /api/github/issues", { query: { repo: repo || undefined, q: q || undefined } }),
      ),
    createIssue: (data: { repo?: string; title: string; body?: string; labels?: string[] }) =>
      untyped<GitHubIssue>(call("POST /api/github/issues", { body: data })),

    getIssue: (number: number, repo?: string) =>
      call("GET /api/github/issues/{number}", {
        path: { number },
        query: { repo: repo || undefined },
      }),
    addIssueComment: (number: number, body: string, repo?: string) =>
      call("POST /api/github/issues/{number}/comments", { path: { number }, body: { body, repo } }),
    setIssueLabels: (number: number, labels: string[], repo?: string) =>
      call("PUT /api/github/issues/{number}/labels", { path: { number }, body: { labels, repo } }),
    listLabels: (repo?: string) =>
      untyped<IssueLabel[]>(call("GET /api/github/labels", { query: { repo: repo || undefined } })),
    // Summaries
    summarizeIssue: (topicId: number, opts: { force?: boolean } = {}) =>
      call("POST /api/topics/{topic_id}/summary", {
        path: { topic_id: topicId },
        query: { force: opts.force || undefined },
      }),

    // Push the topic title/description back to the linked GitHub issue.
    pushIssue: (topicId: number) =>
      call("POST /api/topics/{topic_id}/issue/push", { path: { topic_id: topicId } }),

    // Slash commands
    draftUpdate: (topicId: number, text?: string) =>
      call("POST /api/topics/{topic_id}/commands/gh-update/draft", {
        path: { topic_id: topicId },
        body: { text: text ?? null },
      }) as Promise<CommentDraft>,
    postUpdate: (topicId: number, body: string, noteAttachmentIds: number[] = []) =>
      call("POST /api/topics/{topic_id}/commands/gh-update/post", {
        path: { topic_id: topicId },
        body: { body, note_attachment_ids: noteAttachmentIds },
      }),
    sync: (topicId: number) =>
      call("POST /api/topics/{topic_id}/commands/gh-sync", { path: { topic_id: topicId } }),
    draftCreate: (topicId: number, text?: string) =>
      call("POST /api/topics/{topic_id}/commands/gh-create/draft", {
        path: { topic_id: topicId },
        body: { text: text ?? null },
      }),
    postCreate: (topicId: number, title: string, body: string) =>
      call("POST /api/topics/{topic_id}/commands/gh-create/post", {
        path: { topic_id: topicId },
        body: { title, body },
      }),
    draftClose: (topicId: number, text?: string) =>
      call("POST /api/topics/{topic_id}/commands/gh-close/draft", {
        path: { topic_id: topicId },
        body: { text: text ?? null },
      }) as Promise<CommentDraft>,
    postClose: (
      topicId: number,
      body: string,
      stateReason: "completed" | "not_planned" = "completed",
    ) =>
      call("POST /api/topics/{topic_id}/commands/gh-close/post", {
        path: { topic_id: topicId },
        body: { body, state_reason: stateReason },
      }),
  },

  approvalRules: {
    list: (owner?: { topicId?: number; chatId?: number }) =>
      call("GET /api/approval-rules", {
        query: {
          topic_id: owner?.topicId,
          chat_id: owner?.chatId,
          scope: owner?.topicId == null && owner?.chatId == null ? "global" : undefined,
        },
      }),
    create: (body: ApprovalRuleCreate) => call("POST /api/approval-rules", { body }),
    update: (id: number, body: ApprovalRuleUpdate) =>
      call("PATCH /api/approval-rules/{rule_id}", { path: { rule_id: id }, body }),
    remove: (id: number) => call("DELETE /api/approval-rules/{rule_id}", { path: { rule_id: id } }),
  },

  mcp: {
    // MCP
    list: (probe = true) =>
      untyped<MCPServerStatus[]>(call("GET /api/mcp/servers", { query: { probe } })),
    probe: (name: string) =>
      untyped<MCPServerStatus>(call("POST /api/mcp/servers/{name}/probe", { path: { name } })),
    connect: (name: string) =>
      untyped<MCPServerStatus>(call("POST /api/mcp/servers/{name}/connect", { path: { name } })),
    disconnect: (name: string) =>
      untyped<MCPServerStatus>(call("POST /api/mcp/servers/{name}/disconnect", { path: { name } })),
    refresh: (name: string) =>
      untyped<MCPServerStatus>(call("POST /api/mcp/servers/{name}/refresh", { path: { name } })),
    setWorkiqPreview: (enabled: boolean) =>
      untyped<MCPServerStatus>(call("POST /api/mcp/servers/workiq/preview", { body: { enabled } })),
    // Drive the browser sign-in for an OAuth-protected server: the hosted
    // WorkIQ preview, or any of the Agent 365 workiq-* endpoints.
    reauthenticateWorkiq: (
      name = "workiq",
      opts?: { usePopup?: boolean; silentOnly?: boolean; auto?: boolean },
    ) =>
      untyped<MCPServerStatus & { interaction_required?: boolean; auth_episode?: string | null }>(
        call("POST /api/mcp/servers/{name}/reauthenticate", {
          path: { name },
          query: {
            use_popup: opts?.usePopup || undefined,
            silent_only: opts?.silentOnly || undefined,
            auto: opts?.auto || undefined,
          },
        }),
      ),
    // Abort an in-flight interactive sign-in so the backend releases that
    // server's fixed OAuth loopback port at once (see workiqSignIn.ts).
    cancelReauthenticateWorkiq: (name = "workiq") =>
      untyped<{ cancelled: boolean }>(
        call("POST /api/mcp/servers/{name}/reauthenticate/cancel", { path: { name } }),
      ),
    // Everything the backend knows about the WorkIQ credentials — settings in
    // force, per-credential token/idle/state facts, and its own auth trace.
    // Read on demand by ``window.precursorWorkiqAuthReport()``.
    authDiagnostics: (limit = 300) =>
      untyped<McpAuthDiagnostics>(call("GET /api/mcp/auth/diagnostics", { query: { limit } })),
    create: (data: MCPServerCreate) =>
      untyped<MCPServerStatus>(call("POST /api/mcp/servers/user", { body: data })),
    update: (id: number, data: MCPServerUpdate) =>
      untyped<MCPServerStatus>(
        call("PATCH /api/mcp/servers/user/{server_id}", { path: { server_id: id }, body: data }),
      ),
    remove: (id: number) =>
      call("DELETE /api/mcp/servers/user/{server_id}", { path: { server_id: id } }),
  },

  llm: {
    // LLM
    listModels: (provider?: string) =>
      call("GET /api/llm/models", { query: { provider: provider || undefined } }),
    listProviders: (includeDisabled = false) =>
      call("GET /api/llm/providers", { query: { include_disabled: includeDisabled || undefined } }),
  },

  notes: {
    // /notes — freeform note capture
    rephrase: (topicId: number, text: string, instruction?: string) =>
      call("POST /api/topics/{topic_id}/commands/notes/rephrase", {
        path: { topic_id: topicId },
        body: { text, instruction: instruction ?? null },
      }),
    append: (topicId: number, text: string, attachmentIds: number[] = []) =>
      call("POST /api/topics/{topic_id}/commands/notes/append", {
        path: { topic_id: topicId },
        body: { text, attachment_ids: attachmentIds },
      }),
    getDraft: (topicId: number) =>
      call("GET /api/topics/{topic_id}/commands/notes/draft", { path: { topic_id: topicId } }),
    saveDraft: (topicId: number, text: string) =>
      call("PUT /api/topics/{topic_id}/commands/notes/draft", {
        path: { topic_id: topicId },
        body: { text },
      }),
    clearDraft: (topicId: number) =>
      call("DELETE /api/topics/{topic_id}/commands/notes/draft", { path: { topic_id: topicId } }),
    listAttachments: (topicId: number) =>
      call("GET /api/topics/{topic_id}/commands/notes/attachments", {
        path: { topic_id: topicId },
      }),
    uploadAttachment: (topicId: number, file: File) =>
      call("POST /api/topics/{topic_id}/commands/notes/attachments", {
        path: { topic_id: topicId },
        form: { file },
      }),
    deleteAttachment: (topicId: number, attachmentId: number) =>
      call("DELETE /api/topics/{topic_id}/commands/notes/attachments/{attachment_id}", {
        path: { topic_id: topicId, attachment_id: attachmentId },
      }),
    attachmentUrl: (attachmentId: number) => `/api/notes/attachments/${attachmentId}`,
  },

  plugins: {
    /** Frontend extension descriptors from every enabled plugin. */
    list: () => untyped<PluginDescriptor[]>(call("GET /api/plugins")),
    /** Every installed plugin, enabled or not, for the Settings panel. */
    installed: () => untyped<InstalledPlugin[]>(call("GET /api/plugins/installed")),
    /** The curated directory of installable plugins, bundled with Precursor. */
    catalog: () => untyped<CatalogPlugin[]>(call("GET /api/plugins/catalog")),
    setEnabled: (id: string, enabled: boolean) =>
      call("PUT /api/plugins/installed/{plugin_id}", {
        path: { plugin_id: id },
        body: { enabled },
      }) as Promise<{ id: string; enabled: boolean }>,
    /** How to install into this instance (and whether the app may do it). */
    environment: () => untyped<PluginEnvironment>(call("GET /api/plugins/environment")),
    /**
     * Install a package name, a GitHub repository link or any requirement.
     * `version` picks a release for the first two; omitted, a name installs
     * unpinned and a repository installs its newest release.
     */
    install: (pkg: string, version?: string | null) =>
      untyped<PluginInstallResult>(
        call("POST /api/plugins/install", {
          body: version ? { package: pkg, version } : { package: pkg },
        }),
      ),
    /** Move an installed plugin to `version`, or to its newest release. */
    upgrade: (id: string, version?: string | null) =>
      untyped<PluginInstallResult>(
        call("POST /api/plugins/installed/{plugin_id}/upgrade", {
          path: { plugin_id: id },
          body: version ? { version } : {},
        }),
      ),
    /** Every release of a package name or GitHub repository link. */
    versions: (pkg: string, refresh = false) =>
      untyped<PluginVersions>(
        call("GET /api/plugins/versions", {
          query: { package: pkg, refresh: refresh || undefined },
        }),
      ),
    /** The newest release of each installed plugin, from where it came from. */
    updates: (refresh = false) =>
      untyped<PluginUpdate[]>(
        call("GET /api/plugins/updates", { query: { refresh: refresh || undefined } }),
      ),
    uninstall: (id: string) =>
      untyped<PluginInstallResult>(
        call("DELETE /api/plugins/installed/{plugin_id}", { path: { plugin_id: id } }),
      ),
    /** Restart the server so plugin discovery runs again. */
    restart: () => untyped<{ status: string }>(call("POST /api/plugins/restart")),
    /** A plugin's own settings blob — opaque to core. */
    settings: {
      get: (id: string) =>
        call("GET /api/plugins/installed/{plugin_id}/settings", { path: { plugin_id: id } }),
      put: (id: string, values: Record<string, unknown>) =>
        call("PUT /api/plugins/installed/{plugin_id}/settings", {
          path: { plugin_id: id },
          body: values,
        }),
    },
  },

  skills: {
    // Skills
    list: () => call("GET /api/skills"),
    create: (data: SkillCreate) => call("POST /api/skills", { body: data }),
    update: (name: string, data: SkillUpdate) =>
      call("PATCH /api/skills/{name}", { path: { name }, body: data }),
    migrate: (name: string) => call("POST /api/skills/{name}/migrate", { path: { name } }),
    remove: (name: string) => call("DELETE /api/skills/{name}", { path: { name } }),
    exportUrl: (name: string) =>
      `/api/skills/${encodeURIComponent(name)}/export`,
  },

  roles: {
    // Roles (Assistant personas)
    list: () => call("GET /api/roles"),
    create: (data: RoleCreate) => call("POST /api/roles", { body: data }),
    update: (name: string, data: RoleUpdate) =>
      call("PATCH /api/roles/{name}", { path: { name }, body: data }),
    remove: (name: string) => call("DELETE /api/roles/{name}", { path: { name } }),
  },

  collections: {
    // Collections (topic groupings that filter the sidebar tree)
    list: () => call("GET /api/collections"),
    create: (data: CollectionCreate) => call("POST /api/collections", { body: data }),
    update: (id: number, data: CollectionUpdate) =>
      call("PATCH /api/collections/{collection_id}", { path: { collection_id: id }, body: data }),
    // Topics are never deleted with the collection — they move to `reassignTo`
    // (or the built-in default when omitted).
    remove: (id: number, reassignTo?: number | null) =>
      call("DELETE /api/collections/{collection_id}", {
        path: { collection_id: id },
        query: { reassign_to: reassignTo },
      }),
  },

  memories: {
    // Memories
    list: () => call("GET /api/memories"),
    create: (data: MemoryCreate) => call("POST /api/memories", { body: data }),
    update: (id: number, data: MemoryUpdate) =>
      call("PATCH /api/memories/{memory_id}", { path: { memory_id: id }, body: data }),
    remove: (id: number) => call("DELETE /api/memories/{memory_id}", { path: { memory_id: id } }),
  },

  me: {
    // Current user
    get: () => call("GET /api/me"),
    // Copilot AI-credit usage for the persona menu (null when unavailable).
    copilot: () => call("GET /api/me/copilot"),
  },

  meetings: {
    // Live meeting sessions
    listSessions: () => call("GET /api/live") as Promise<MeetingSession[]>,
    getSession: (id: number) =>
      call("GET /api/live/{session_id}", { path: { session_id: id } }) as Promise<MeetingSession>,
    createSession: (data: MeetingSessionCreate) =>
      call("POST /api/live", { body: data }) as Promise<MeetingSession>,
    updateSession: (id: number, data: MeetingSessionUpdate) =>
      call("PATCH /api/live/{session_id}", {
        path: { session_id: id },
        body: data,
      }) as Promise<MeetingSession>,
    deleteSession: (id: number) =>
      call("DELETE /api/live/{session_id}", { path: { session_id: id } }),
    listArchivedSessions: () => call("GET /api/live/archived") as Promise<MeetingSession[]>,
    archiveSession: (id: number) =>
      call("POST /api/live/{session_id}/archive", {
        path: { session_id: id },
      }) as Promise<MeetingSession>,
    unarchiveSession: (id: number) =>
      call("POST /api/live/{session_id}/unarchive", {
        path: { session_id: id },
      }) as Promise<MeetingSession>,
    listSegments: (id: number) =>
      call("GET /api/live/{session_id}/segments", { path: { session_id: id } }),
    appendSegment: (id: number, data: MeetingSegmentCreate) =>
      call("POST /api/live/{session_id}/segments", { path: { session_id: id }, body: data }),
    updateSegment: (id: number, segmentId: number, data: MeetingSegmentUpdate) =>
      call("PATCH /api/live/{session_id}/segments/{segment_id}", {
        path: { session_id: id, segment_id: segmentId },
        body: data,
      }),
    renameSpeaker: (id: number, label: string, name: string) =>
      call("POST /api/live/{session_id}/speakers", {
        path: { session_id: id },
        body: { label, name },
      }) as Promise<MeetingSession>,
    markSpeakerMe: (id: number, label: string, me: boolean) =>
      call("POST /api/live/{session_id}/speakers/me", {
        path: { session_id: id },
        body: { label, me },
      }) as Promise<MeetingSession>,
    setAttendees: (id: number, attendees: string[]) =>
      call("PUT /api/live/{session_id}/attendees", {
        path: { session_id: id },
        body: { attendees },
      }) as Promise<MeetingSession>,
    addContextNote: (id: number, text: string) =>
      call("POST /api/live/{session_id}/context-notes", {
        path: { session_id: id },
        body: { text },
      }) as Promise<MeetingSession>,
    setContextNotes: (id: number, notes: string[]) =>
      call("PUT /api/live/{session_id}/context-notes", {
        path: { session_id: id },
        body: { notes },
      }) as Promise<MeetingSession>,
    uploadAttachment: (id: number, file: File) =>
      call("POST /api/live/{session_id}/attachments", { path: { session_id: id }, form: { file } }),
    ensureChat: (id: number) =>
      call("POST /api/live/{session_id}/chat", { path: { session_id: id } }),
    setFeatures: (id: number, features: string[]) =>
      call("PATCH /api/live/{session_id}", {
        path: { session_id: id },
        body: { features },
      }) as Promise<MeetingSession>,
    translate: (id: number, targetLang: string, texts?: string[]) =>
      call("POST /api/live/{session_id}/translate", {
        path: { session_id: id },
        body: { target_lang: targetLang, texts },
      }),
    listInsights: (id: number) =>
      call("GET /api/live/{session_id}/insights", { path: { session_id: id } }),
    analyze: (id: number) =>
      call("POST /api/live/{session_id}/analyze", { path: { session_id: id } }),
    summarize: (id: number, options: MeetingSummaryOptions = {}) =>
      call("POST /api/live/{session_id}/summary", { path: { session_id: id }, body: options }),
    /** Summary templates (built in + definition files), languages, last used. */
    summaryTemplates: () => call("GET /api/live/summary-templates"),
    /**
     * The definition file to edit a template in — a built-in is saved to the
     * folder first; `duplicate` saves it as a new template instead.
     */
    /** Remember the picker's template + language for the next recap. */
    selectSummaryTemplate: (template: string, language: string) =>
      call("PUT /api/live/summary-templates/selection", { body: { template, language } }),
    summaryTemplateFile: (template: string, duplicate = false) =>
      call("POST /api/live/summary-templates/files", { body: { template, duplicate } }),
    /** The linked Teams meeting's transcription sessions (fail-closed). */
    listTranscripts: (id: number) =>
      call("GET /api/live/{session_id}/transcripts", { path: { session_id: id } }),
    summarizeFromTranscript: (
      id: number,
      transcriptIds?: string[],
      options: MeetingSummaryOptions = {},
    ) =>
      call("POST /api/live/{session_id}/summary/from-transcript", {
        path: { session_id: id },
        body: { transcript_ids: transcriptIds ?? [], ...options },
      }),
    postSummary: (id: number, summary: string) =>
      call("POST /api/live/{session_id}/summary/post", {
        path: { session_id: id },
        body: { summary },
      }),
    topicContextSummary: (id: number) =>
      call("POST /api/live/{session_id}/topic-summary", { path: { session_id: id } }),
    getAgenda: (pastDays = 7) => {
      // The agenda window: from local midnight ``pastDays`` ago through the next
      // local midnight, so past meetings (for a "record from a past meeting"
      // flow) show alongside today's — matched to the user's calendar day and
      // converted to UTC ISO.
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - pastDays);
      const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
      return call("GET /api/live/m365/agenda", {
        query: { start: start.toISOString(), end: end.toISOString() },
      });
    },
    link: (id: number, event: AgendaEvent) =>
      call("POST /api/live/{session_id}/meeting", { path: { session_id: id }, body: {
          id: event.id,
          subject: event.subject,
          start: event.start,
          end: event.end,
          organizer: event.organizer,
          attendees: event.attendees,
          is_online: event.is_online,
          join_url: event.join_url,
          body_preview: event.body_preview,
        } }) as Promise<MeetingSession>,
    unlink: (id: number) =>
      call("DELETE /api/live/{session_id}/meeting", {
        path: { session_id: id },
      }) as Promise<MeetingSession>,
    postToTopic: (id: number) =>
      call("POST /api/live/{session_id}/meeting/post", { path: { session_id: id } }),
  },

  artifacts: {
    // Workspace files attached to a topic or chat.
    list: (kind: ReminderContainer, id: number) =>
      (kind === "topic"
        ? call("GET /api/topics/{topic_id}/artifacts", { path: { topic_id: id } })
        : call("GET /api/chats/{chat_id}/artifacts", { path: { chat_id: id } })
      ) as Promise<ConversationArtifact[]>,
    attach: (
      kind: ReminderContainer,
      id: number,
      data: { workspace_id: number; path: string; source?: "manual" | "opened" },
    ) =>
      (kind === "topic"
        ? call("POST /api/topics/{topic_id}/artifacts", { path: { topic_id: id }, body: data })
        : call("POST /api/chats/{chat_id}/artifacts", { path: { chat_id: id }, body: data })
      ) as Promise<ConversationArtifact>,
    detach: (kind: ReminderContainer, id: number, artifactId: number) =>
      kind === "topic"
        ? call("DELETE /api/topics/{topic_id}/artifacts/{artifact_id}", {
            path: { topic_id: id, artifact_id: artifactId },
          })
        : call("DELETE /api/chats/{chat_id}/artifacts/{artifact_id}", {
            path: { chat_id: id, artifact_id: artifactId },
          }),
    // Topics and chats attaching files of a workspace; the ones on `path` lead.
    discussions: (workspaceId: number, path?: string | null) =>
      call("GET /api/workspaces/{workspace_id}/discussions", {
        path: { workspace_id: workspaceId },
        query: { path: path || undefined },
      }),
  },

  workspaces: {
    // Workspaces
    list: () => call("GET /api/workspaces") as Promise<Workspace[]>,
    create: (data: WorkspaceCreate) =>
      call("POST /api/workspaces", { body: data }) as Promise<Workspace>,
    remove: (id: number) =>
      call("DELETE /api/workspaces/{workspace_id}", { path: { workspace_id: id } }),
    listFiles: (workspaceId: number) =>
      call("GET /api/workspaces/{workspace_id}/files", { path: { workspace_id: workspaceId } }),
    readFile: (workspaceId: number, path: string) =>
      call("GET /api/workspaces/{workspace_id}/file", {
        path: { workspace_id: workspaceId },
        query: { path },
      }),
    writeFile: (workspaceId: number, path: string, content: string) =>
      call("PUT /api/workspaces/{workspace_id}/file", {
        path: { workspace_id: workspaceId },
        query: { path },
        body: { content },
      }),
    createFile: (workspaceId: number, path: string, content = "") =>
      call("POST /api/workspaces/{workspace_id}/file", {
        path: { workspace_id: workspaceId },
        body: { path, content },
      }),
    createFolder: (workspaceId: number, path: string) =>
      call("POST /api/workspaces/{workspace_id}/folder", {
        path: { workspace_id: workspaceId },
        body: { path },
      }),
    deleteFile: (workspaceId: number, path: string) =>
      call("DELETE /api/workspaces/{workspace_id}/file", {
        path: { workspace_id: workspaceId },
        query: { path },
      }),
    // A folder and everything in it (never the workspace's own folder).
    deleteFolder: (workspaceId: number, path: string) =>
      call("DELETE /api/workspaces/{workspace_id}/folder", {
        path: { workspace_id: workspaceId },
        query: { path },
      }),
    renameEntry: (workspaceId: number, path: string, newPath: string) =>
      call("POST /api/workspaces/{workspace_id}/rename", {
        path: { workspace_id: workspaceId },
        body: { path, new_path: newPath },
      }),
    gitStatus: (workspaceId: number) =>
      call("GET /api/workspaces/{workspace_id}/git/status", {
        path: { workspace_id: workspaceId },
      }),
    // Updates the remote-tracking branch (ahead/behind); changes no file.
    gitFetch: (workspaceId: number) =>
      call("POST /api/workspaces/{workspace_id}/git/fetch", {
        path: { workspace_id: workspaceId },
      }),
    gitPull: (workspaceId: number) =>
      call("POST /api/workspaces/{workspace_id}/git/pull", { path: { workspace_id: workspaceId } }),
    // Commit locally; `paths` selects files, otherwise every change.
    gitCommit: (workspaceId: number, message: string, paths?: string[]) =>
      call("POST /api/workspaces/{workspace_id}/git/commit", {
        path: { workspace_id: workspaceId },
        body: paths ? { message, paths } : { message },
      }),
    // Push the checked-out branch, publishing it the first time.
    gitPush: (workspaceId: number) =>
      call("POST /api/workspaces/{workspace_id}/git/push", { path: { workspace_id: workspaceId } }),
    gitCommitPush: (
      workspaceId: number,
      message: string,
      paths?: string[],
    ) =>
      call("POST /api/workspaces/{workspace_id}/git/commit-push", {
        path: { workspace_id: workspaceId },
        body: paths ? { message, paths } : { message },
      }),
    gitDiscard: (workspaceId: number, path: string) =>
      call("POST /api/workspaces/{workspace_id}/git/discard", {
        path: { workspace_id: workspaceId },
        query: { path },
      }),
    // A file at `base` (HEAD) and in the working copy — or at `head` — for the
    // diff editor. `originalPath` is a rename's old name.
    // With `head` and no `base` (a root commit), the original side is empty.
    gitFileVersions: (
      workspaceId: number,
      path: string,
      opts: { originalPath?: string | null; base?: string | null; head?: string } = {},
    ) =>
      call("GET /api/workspaces/{workspace_id}/git/file-versions", {
        path: { workspace_id: workspaceId },
        query: {
          path,
          original_path: opts.originalPath || undefined,
          base: opts.base || undefined,
          head: opts.head || undefined,
        },
      }),
    // A page of history, newest first; `path` limits it to one file (by its
    // repository path), following renames.
    gitLog: (workspaceId: number, opts: { limit?: number; skip?: number; path?: string | null } = {}) =>
      call("GET /api/workspaces/{workspace_id}/git/log", {
        path: { workspace_id: workspaceId },
        query: {
          limit: opts.limit || undefined,
          skip: opts.skip || undefined,
          path: opts.path || undefined,
        },
      }),
    // Local branches and the remote's (asked of the remote itself).
    gitBranches: (workspaceId: number) =>
      call("GET /api/workspaces/{workspace_id}/git/branches", {
        path: { workspace_id: workspaceId },
      }),
    // Check out a branch (local, or only on the remote). 409 names what stands
    // in the way: nothing is ever forced or stashed.
    gitSwitch: (workspaceId: number, name: string) =>
      call("POST /api/workspaces/{workspace_id}/git/switch", {
        path: { workspace_id: workspaceId },
        body: { name },
      }),
    // Create a branch from HEAD and check it out (unpublished until pushed).
    gitCreateBranch: (workspaceId: number, name: string) =>
      call("POST /api/workspaces/{workspace_id}/git/branches", {
        path: { workspace_id: workspaceId },
        body: { name },
      }),
    // Merge the remote's branch; `ok: false` with `status.merging` means it
    // stopped on conflicts.
    gitMerge: (workspaceId: number) =>
      call("POST /api/workspaces/{workspace_id}/git/merge", {
        path: { workspace_id: workspaceId },
      }),
    gitConflict: (workspaceId: number, path: string) =>
      call("GET /api/workspaces/{workspace_id}/git/conflict", {
        path: { workspace_id: workspaceId },
        query: { path },
      }),
    // Mark a conflict resolved: as edited (refused while markers remain), or
    // by keeping one side.
    gitResolve: (workspaceId: number, path: string, side?: "ours" | "theirs") =>
      call("POST /api/workspaces/{workspace_id}/git/resolve", {
        path: { workspace_id: workspaceId },
        body: side ? { path, side } : { path },
      }),
    gitMergeComplete: (workspaceId: number) =>
      call("POST /api/workspaces/{workspace_id}/git/merge/complete", {
        path: { workspace_id: workspaceId },
      }),
    gitMergeAbort: (workspaceId: number) =>
      call("POST /api/workspaces/{workspace_id}/git/merge/abort", {
        path: { workspace_id: workspaceId },
      }),
    // One commit: message, first parent and changed files.
    gitCommitDetail: (workspaceId: number, sha: string) =>
      call("GET /api/workspaces/{workspace_id}/git/commits/{sha}", {
        path: { workspace_id: workspaceId, sha },
      }),
    gitDiff: (workspaceId: number, path: string) =>
      call("GET /api/workspaces/{workspace_id}/git/diff", {
        path: { workspace_id: workspaceId },
        query: { path },
      }),
    localPath: (workspaceId: number) =>
      call("GET /api/workspaces/{workspace_id}/local-path", {
        path: { workspace_id: workspaceId },
      }),
  },

  drawio: {
    // Self-hosted diagrams.net webapp, downloaded on demand into the data dir.
    status: () => call("GET /api/drawio/status"),
    install: () => call("POST /api/drawio/install"),
  },

  system: {
    // Version
    getVersion: () => call("GET /api/version"),

    // Usage statistics
    getUsageStats: () => call("GET /api/stats/usage"),
    getSystemStats: () => call("GET /api/stats/system"),

    // Storage cockpit: what each retention sweep would free, running one on
    // demand, and returning freed pages to the filesystem.
    getCleanupPreview: () => call("GET /api/stats/cleanup") as Promise<CleanupPreview>,
    runCleanup: (key: string) => call("POST /api/stats/cleanup/{key}", { path: { key } }),
    compactDatabase: () => call("POST /api/stats/compact"),
  },

  search: {
    // Cross-entity content search backing the ⌘K palette.
    query: (q: string, limit = 40) => call("GET /api/search", { query: { q, limit } }),
  },

  iq: {
    // Precursor IQ: ranked retrieval (the ⌘K palette), cited answers, index state.
    retrieve: (q: string, sections: IQSection[] = [], limit = 20) =>
      call("GET /api/iq/retrieve", {
        query: { q, limit, sections: sections.length ? sections.join(",") : undefined },
      }),
    ask: (question: string, sections?: IQSection[]) =>
      call("POST /api/iq/ask", { body: { question, sections: sections ?? null } }),
    status: () => call("GET /api/iq/status"),
    reindex: () => call("POST /api/iq/reindex"),
    // Tries the unsaved provider/model; omitted fields fall back to the saved ones.
    testEmbeddings: (provider?: string, model?: string) =>
      call("POST /api/iq/embeddings/test", { body: { provider, model } }),
  },

  topicSummary: {
    // The topic's editable status brief (issue #316). Resolves to null while the
    // topic has never had one — generation is always user-initiated.
    get: (topicId: number) =>
      call("GET /api/topics/{topic_id}/topic-summary", { path: { topic_id: topicId } }),
    save: (topicId: number, content: string, revision: string) =>
      call("PUT /api/topics/{topic_id}/topic-summary", {
        path: { topic_id: topicId },
        body: { content, revision },
      }),
    // Hiding a topic that never had a summary resolves to null: nothing is
    // created just to be collapsed.
    setVisible: (topicId: number, visible: boolean) =>
      call("POST /api/topics/{topic_id}/topic-summary/visibility", {
        path: { topic_id: topicId },
        body: { visible },
      }),
    // Regenerates from the conversation, notes and attachments. When the
    // summary was hand-edited the answer comes back as `suggestion` to review
    // instead of replacing `content`.
    generate: (topicId: number, instruction?: string) =>
      call("POST /api/topics/{topic_id}/topic-summary/generate", {
        path: { topic_id: topicId },
        body: { instruction: instruction ?? null },
      }),
    resolve: (topicId: number, accepted: number[], revision: string, reviewed?: number[]) => {
      const payload: TopicSummaryResolve = { accepted, revision, reviewed };
      return call("POST /api/topics/{topic_id}/topic-summary/resolve", {
        path: { topic_id: topicId },
        body: payload,
      });
    },
    addItem: (topicId: number, kind: "todo" | "important", text: string) =>
      call("POST /api/topics/{topic_id}/topic-summary/items", {
        path: { topic_id: topicId },
        body: { kind, text },
      }),
    remove: (topicId: number, revision: string) =>
      call("DELETE /api/topics/{topic_id}/topic-summary", {
        path: { topic_id: topicId },
        query: { revision },
      }),
  },

  ai: {
    // Rewrite a block of user-authored text. Backs the "Refine with AI"
    // affordance on textareas across the app.
    refine: (data: RefineRequest) => call("POST /api/refine", { body: data }),
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
