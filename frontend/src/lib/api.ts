import type {
  AgentEvent,
  AgentLink,
  AgentModelInfo,
  AgentPermissionDecisionValue,
  AgentPermissionGrant,
  AgentSchedule,
  AgentScheduleCreate,
  AgentScheduleUpdate,
  AgentSession,
  AgentSessionCreate,
  AppVersion,
  BackupRunResult,
  Attachment,
  Chat,
  ChatCreate,
  ChatUpdate,
  CommentDraft,
  CommentPostResult,
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
  ItemStatusResult,
  LLMModel,
  LLMProviderSpec,
  LocalPath,
  MCPServerCreate,
  MCPServerStatus,
  MCPServerUpdate,
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
  MeetingSession,
  MeetingSessionCreate,
  MeetingSessionUpdate,
  Message,
  NotesDraft,
  NoteDraftAttachment,
  PluginDescriptor,
  ProjectBoard,
  ProjectSummary,
  IssueDetail,
  Reminder,  ReminderContainer,
  ReminderCreate,
  ReminderItem,
  Role,
  RoleCreate,
  RoleUpdate,
  Schedule,
  ScheduleUpdate,
  TopicScheduleCreate,
  Settings,
  SettingsUpdate,
  Skill,
  SkillCreate,
  SkillUpdate,
  SystemStats,
  Topic,
  TopicNode,
  UsageStats,
  Workspace,
  WorkspaceCreate,
  WorkspaceFileContent,
  WorkspaceFileNode,
  WorkspaceUpdate,
} from "./types";
import { CLIENT_ID } from "./clientId";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": CLIENT_ID,
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// FastAPI errors are thrown by `request` as "<status> <statusText>: <body>",
// where the body is usually `{"detail": "..."}`. Surface just that detail so
// UI error states read cleanly instead of echoing the raw HTTP prefix + JSON.
export function apiErrorMessage(e: unknown, fallback = "Something went wrong"): string {
  if (!(e instanceof Error)) return fallback;
  const idx = e.message.indexOf(": ");
  const body = idx >= 0 ? e.message.slice(idx + 2) : e.message;
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // Not JSON — fall through to the raw message.
  }
  return e.message || fallback;
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
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
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
    list: (q?: string) =>
      request<Topic[]>(`/api/topics${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    tree: () => request<TopicNode[]>(`/api/topics/tree`),
    get: (id: number) => request<Topic>(`/api/topics/${id}`),
    getBySlug: (slug: string) =>
      request<Topic>(`/api/topics/by-slug/${encodeURIComponent(slug)}`),
    create: (data: Partial<Topic> & { create_linked_issue?: boolean }) =>
      request<Topic>(`/api/topics`, { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Topic>) =>
      request<Topic>(`/api/topics/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: number) => request<void>(`/api/topics/${id}`, { method: "DELETE" }),
    markRead: (id: number) =>
      request<void>(`/api/topics/${id}/read`, { method: "POST" }),
    listArchived: () => request<Topic[]>(`/api/topics/archived`),
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
    archive: (id: number) =>
      request<Chat>(`/api/chats/${id}/archive`, { method: "POST" }),
    unarchive: (id: number) =>
      request<Chat>(`/api/chats/${id}/unarchive`, { method: "POST" }),
    // Promote a flat chat into a full topic (moves the transcript over).
    promote: (id: number) =>
      request<Topic>(`/api/chats/${id}/promote`, { method: "POST" }),

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
    get: (container: ReminderContainer, id: number) =>
      request<Reminder>(`/api/reminders/${container}/${id}`),
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
    create: (data: AgentSessionCreate) =>
      request<AgentSession>(`/api/agents`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    getEvents: (id: number) => request<AgentEvent[]>(`/api/agents/${id}/events`),
    listModels: () => request<AgentModelInfo[]>(`/api/agents/models`),
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
    update: (id: number, payload: { title?: string; task?: string }) =>
      request<AgentSession>(`/api/agents/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    remove: (id: number) => request<void>(`/api/agents/${id}`, { method: "DELETE" }),
    listArchived: () => request<AgentSession[]>(`/api/agents/archived`),
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

    // Projects v2 (kanban board)
    listProjects: (repo?: string) => {
      const qs = repo ? `?repo=${encodeURIComponent(repo)}` : "";
      return request<ProjectSummary[]>(`/api/github/projects${qs}`);
    },
    projectBoard: (projectId: string) =>
      request<ProjectBoard>(`/api/github/projects/${encodeURIComponent(projectId)}/board`),
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
    setProjectItemStatus: (
      projectId: string,
      itemId: string,
      data: { field_id: string; option_id: string },
    ) =>
      request<ItemStatusResult>(
        `/api/github/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(
          itemId,
        )}/status`,
        { method: "POST", body: JSON.stringify(data) },
      ),

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
    reauthenticateWorkiq: (opts?: { usePopup?: boolean }) =>
      request<MCPServerStatus>(
        `/api/mcp/servers/workiq/reauthenticate${opts?.usePopup ? "?use_popup=true" : ""}`,
        { method: "POST" },
      ),
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
    // Plugins
    list: () => request<PluginDescriptor[]>(`/api/plugins`),
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
    postSummary: (id: number, summary: string) =>
      request<{ topic_id: number; message_id: number; posted_at: string }>(
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
    getAgenda: () => {
      // The user's *local* day window (local midnight → next local midnight),
      // converted to UTC ISO so today's meetings match their calendar day.
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
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
          subject: event.subject,
          start: event.start,
          end: event.end,
          organizer: event.organizer,
          attendees: event.attendees,
          is_online: event.is_online,
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

  system: {
    // Version
    getVersion: () => request<AppVersion>(`/api/version`),

    // Usage statistics
    getUsageStats: () => request<UsageStats>(`/api/stats/usage`),
    getSystemStats: () => request<SystemStats>(`/api/stats/system`),
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
