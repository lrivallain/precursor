import type {
  AgentEvent,
  AgentLink,
  AgentModelInfo,
  AgentPermissionDecisionValue,
  AgentPermissionGrant,
  AgentSession,
  AgentSessionCreate,
  AppVersion,
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
  IssuePushResult,
  IssueSummary,
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
  Message,
  NotesDraft,
  NoteDraftAttachment,
  PluginDescriptor,
  Reminder,
  ReminderContainer,
  ReminderCreate,
  ReminderItem,
  Role,
  RoleCreate,
  RoleUpdate,
  Schedule,
  ScheduleCreate,
  ScheduleUpdate,
  Settings,
  SettingsUpdate,
  Skill,
  SkillCreate,
  SkillUpdate,
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

export const api = {
  // Topics
  listTopics: (q?: string) =>
    request<Topic[]>(`/api/topics${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  topicTree: () => request<TopicNode[]>(`/api/topics/tree`),
  getTopic: (id: number) => request<Topic>(`/api/topics/${id}`),
  getTopicBySlug: (slug: string) =>
    request<Topic>(`/api/topics/by-slug/${encodeURIComponent(slug)}`),
  createTopic: (data: Partial<Topic>) =>
    request<Topic>(`/api/topics`, { method: "POST", body: JSON.stringify(data) }),
  updateTopic: (id: number, data: Partial<Topic>) =>
    request<Topic>(`/api/topics/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteTopic: (id: number) => request<void>(`/api/topics/${id}`, { method: "DELETE" }),
  markTopicRead: (id: number) =>
    request<void>(`/api/topics/${id}/read`, { method: "POST" }),
  listArchivedTopics: () => request<Topic[]>(`/api/topics/archived`),
  archiveTopic: (id: number) =>
    request<Topic>(`/api/topics/${id}/archive`, { method: "POST" }),
  unarchiveTopic: (id: number) =>
    request<Topic>(`/api/topics/${id}/unarchive`, { method: "POST" }),

  // Chats (flat conversation sessions — no tree, no GitHub link)
  listChats: (q?: string) =>
    request<Chat[]>(`/api/chats${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  listArchivedChats: () => request<Chat[]>(`/api/chats/archived`),
  getChat: (id: number) => request<Chat>(`/api/chats/${id}`),
  getChatBySlug: (slug: string) =>
    request<Chat>(`/api/chats/by-slug/${encodeURIComponent(slug)}`),
  createChat: (data: ChatCreate) =>
    request<Chat>(`/api/chats`, { method: "POST", body: JSON.stringify(data) }),
  updateChat: (id: number, data: ChatUpdate) =>
    request<Chat>(`/api/chats/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteChat: (id: number) => request<void>(`/api/chats/${id}`, { method: "DELETE" }),
  markChatRead: (id: number) => request<void>(`/api/chats/${id}/read`, { method: "POST" }),
  archiveChat: (id: number) =>
    request<Chat>(`/api/chats/${id}/archive`, { method: "POST" }),
  unarchiveChat: (id: number) =>
    request<Chat>(`/api/chats/${id}/unarchive`, { method: "POST" }),
  // Promote a flat chat into a full topic (moves the transcript over).
  promoteChat: (id: number) =>
    request<Topic>(`/api/chats/${id}/promote`, { method: "POST" }),

  // Chat messages (mirror topic message endpoints)
  listChatMessages: (chatId: number) =>
    request<Message[]>(`/api/chats/${chatId}/messages`),
  clearChatMessages: (chatId: number) =>
    request<void>(`/api/chats/${chatId}/messages`, { method: "DELETE" }),
  deleteChatMessage: (chatId: number, messageId: number) =>
    request<void>(`/api/chats/${chatId}/messages/${messageId}`, { method: "DELETE" }),
  saveStoppedChatMessage: (chatId: number, content: string) =>
    request<Message>(`/api/chats/${chatId}/messages/stopped`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  // /notes for chats (no GitHub comment option)
  rephraseChatNotes: (chatId: number, text: string, instruction?: string) =>
    request<{ text: string }>(`/api/chats/${chatId}/messages/notes/rephrase`, {
      method: "POST",
      body: JSON.stringify({ text, instruction: instruction ?? null }),
    }),
  appendChatNotes: (chatId: number, text: string, attachmentIds: number[] = []) =>
    request<{ message: Message }>(`/api/chats/${chatId}/messages/notes/append`, {
      method: "POST",
      body: JSON.stringify({ text, attachment_ids: attachmentIds }),
    }),
  getChatNotesDraft: (chatId: number) =>
    request<NotesDraft>(`/api/chats/${chatId}/messages/notes/draft`),
  saveChatNotesDraft: (chatId: number, text: string) =>
    request<NotesDraft>(`/api/chats/${chatId}/messages/notes/draft`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),
  clearChatNotesDraft: (chatId: number) =>
    request<void>(`/api/chats/${chatId}/messages/notes/draft`, { method: "DELETE" }),
  listChatNoteAttachments: (chatId: number) =>
    request<NoteDraftAttachment[]>(`/api/chats/${chatId}/messages/notes/attachments`),
  uploadChatNoteAttachment: async (
    chatId: number,
    file: File,
  ): Promise<NoteDraftAttachment> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`/api/chats/${chatId}/messages/notes/attachments`, {
      method: "POST",
      headers: { "X-Client-Id": CLIENT_ID },
      body: form,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return (await res.json()) as NoteDraftAttachment;
  },
  deleteChatNoteAttachment: (chatId: number, attachmentId: number) =>
    request<void>(`/api/chats/${chatId}/messages/notes/attachments/${attachmentId}`, {
      method: "DELETE",
    }),

  // Schedules (recurring automation topics). Keyed by topic id.
  getSchedule: (topicId: number) =>
    request<Schedule>(`/api/schedules/${topicId}`),
  createSchedule: (data: ScheduleCreate) =>
    request<Schedule>(`/api/schedules`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateSchedule: (topicId: number, data: ScheduleUpdate) =>
    request<Schedule>(`/api/schedules/${topicId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteSchedule: (topicId: number) =>
    request<void>(`/api/schedules/${topicId}`, { method: "DELETE" }),
  runScheduleNow: (topicId: number) =>
    request<Schedule>(`/api/schedules/${topicId}/run`, { method: "POST" }),

  // Reminders (one-shot date/time). Keyed by container kind + id; shared by
  // topics and chats. listReminders returns only fired (awaiting acknowledgment).
  listReminders: () => request<ReminderItem[]>(`/api/reminders`),
  getReminder: (container: ReminderContainer, id: number) =>
    request<Reminder>(`/api/reminders/${container}/${id}`),
  setReminder: (container: ReminderContainer, id: number, data: ReminderCreate) =>
    request<Reminder>(`/api/reminders/${container}/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  // Used for both /reminder-cancel (pending) and /done (fired) — both delete.
  clearReminder: (container: ReminderContainer, id: number) =>
    request<void>(`/api/reminders/${container}/${id}`, { method: "DELETE" }),

  // Agents mode (Copilot SDK). Long-running agent sessions, optionally attached
  // to a topic/chat. Live progress arrives via the `agent.changed` SSE event;
  // the step timeline is re-fetched from `/events` on each signal.
  listAgents: (filter?: { topicId?: number; chatId?: number }) => {
    const qs = new URLSearchParams();
    if (filter?.topicId != null) qs.set("topic_id", String(filter.topicId));
    if (filter?.chatId != null) qs.set("chat_id", String(filter.chatId));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<AgentSession[]>(`/api/agents${suffix}`);
  },
  getAgent: (id: number | string) => request<AgentSession>(`/api/agents/${id}`),
  createAgent: (data: AgentSessionCreate) =>
    request<AgentSession>(`/api/agents`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getAgentEvents: (id: number) => request<AgentEvent[]>(`/api/agents/${id}/events`),
  listAgentModels: () => request<AgentModelInfo[]>(`/api/agents/models`),
  listAgentPermissions: () => request<AgentPermissionGrant[]>(`/api/agents/permissions`),
  resetAgentPermissions: () =>
    request<{ cleared: number }>(`/api/agents/permissions/reset`, { method: "POST" }),
  sendToAgent: (id: number | string, message: string) =>
    request<AgentSession>(`/api/agents/${id}/send`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  cancelAgent: (id: number) =>
    request<AgentSession>(`/api/agents/${id}/cancel`, { method: "POST" }),
  resumeAgent: (id: number | string) =>
    request<AgentSession>(`/api/agents/${id}/resume`, { method: "POST" }),
  resolveAgentPermission: (
    id: number,
    requestId: string,
    decision: AgentPermissionDecisionValue,
  ) =>
    request<AgentSession>(`/api/agents/${id}/permission`, {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, decision }),
    }),
  linkAgent: (id: number, link: AgentLink) =>
    request<AgentSession>(`/api/agents/${id}/link`, {
      method: "PATCH",
      body: JSON.stringify({ topic_id: link.topic_id ?? null, chat_id: link.chat_id ?? null }),
    }),
  renameAgent: (id: number, title: string) =>
    request<AgentSession>(`/api/agents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteAgent: (id: number) => request<void>(`/api/agents/${id}`, { method: "DELETE" }),
  listArchivedAgents: () => request<AgentSession[]>(`/api/agents/archived`),
  archiveAgent: (id: number) =>
    request<AgentSession>(`/api/agents/${id}/archive`, { method: "POST" }),
  unarchiveAgent: (id: number) =>
    request<AgentSession>(`/api/agents/${id}/unarchive`, { method: "POST" }),

  // Messages
  listMessages: (topicId: number) =>
    request<Message[]>(`/api/topics/${topicId}/messages`),
  clearMessages: (topicId: number) =>
    request<void>(`/api/topics/${topicId}/messages`, { method: "DELETE" }),
  deleteMessage: (topicId: number, messageId: number) =>
    request<void>(`/api/topics/${topicId}/messages/${messageId}`, {
      method: "DELETE",
    }),
  // Persist a partial assistant reply when the user stops generation.
  saveStoppedMessage: (topicId: number, content: string) =>
    request<Message>(`/api/topics/${topicId}/messages/stopped`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),

  // Attachments (images + selected documents)
  uploadAttachment: async (topicId: number, file: File): Promise<Attachment> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`/api/topics/${topicId}/attachments`, {
      method: "POST",
      headers: { "X-Client-Id": CLIENT_ID },
      body: form,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return (await res.json()) as Attachment;
  },
  uploadChatAttachment: async (chatId: number, file: File): Promise<Attachment> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`/api/chats/${chatId}/attachments`, {
      method: "POST",
      headers: { "X-Client-Id": CLIENT_ID },
      body: form,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return (await res.json()) as Attachment;
  },
  deleteAttachment: (attachmentId: number) =>
    request<void>(`/api/attachments/${attachmentId}`, { method: "DELETE" }),
  attachmentUrl: (attachmentId: number) => `/api/attachments/${attachmentId}`,

  // Settings
  getSettings: () => request<Settings>(`/api/settings`),
  updateSettings: (data: SettingsUpdate) =>
    request<Settings>(`/api/settings`, { method: "PUT", body: JSON.stringify(data) }),

  // Version
  getVersion: () => request<AppVersion>(`/api/version`),

  // Speech-to-text (Azure token broker)
  getSttToken: () =>
    request<{ token: string; endpoint: string; language: string }>(`/api/stt/token`),
  testSttConnection: (endpoint: string, key?: string) =>
    request<{ ok: boolean; detail: string | null }>(`/api/stt/test`, {
      method: "POST",
      body: JSON.stringify({ endpoint, key: key || null }),
    }),

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

  // MCP
  listMcpServers: () => request<MCPServerStatus[]>(`/api/mcp/servers`),
  connectMcpServer: (name: string) =>
    request<MCPServerStatus>(`/api/mcp/servers/${name}/connect`, { method: "POST" }),
  disconnectMcpServer: (name: string) =>
    request<MCPServerStatus>(`/api/mcp/servers/${name}/disconnect`, { method: "POST" }),
  setWorkiqPreview: (enabled: boolean) =>
    request<MCPServerStatus>(`/api/mcp/servers/workiq/preview`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  reauthenticateWorkiq: () =>
    request<MCPServerStatus>(`/api/mcp/servers/workiq/reauthenticate`, {
      method: "POST",
    }),
  createMcpServer: (data: MCPServerCreate) =>
    request<MCPServerStatus>(`/api/mcp/servers/user`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateMcpServer: (id: number, data: MCPServerUpdate) =>
    request<MCPServerStatus>(`/api/mcp/servers/user/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteMcpServer: (id: number) =>
    request<void>(`/api/mcp/servers/user/${id}`, { method: "DELETE" }),

  // LLM
  listModels: (provider?: string) =>
    request<LLMModel[]>(
      `/api/llm/models${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`,
    ),
  listProviders: () => request<LLMProviderSpec[]>(`/api/llm/providers`),

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
  draftGhUpdate: (topicId: number, text?: string) =>
    request<CommentDraft>(`/api/topics/${topicId}/commands/gh-update/draft`, {
      method: "POST",
      body: JSON.stringify({ text: text ?? null }),
    }),
  postGhUpdate: (topicId: number, body: string, noteAttachmentIds: number[] = []) =>
    request<CommentPostResult>(`/api/topics/${topicId}/commands/gh-update/post`, {
      method: "POST",
      body: JSON.stringify({ body, note_attachment_ids: noteAttachmentIds }),
    }),
  syncGh: (topicId: number) =>
    request<GhSyncResult>(`/api/topics/${topicId}/commands/gh-sync`, {
      method: "POST",
    }),
  draftGhCreate: (topicId: number, text?: string) =>
    request<GhCreateDraft>(`/api/topics/${topicId}/commands/gh-create/draft`, {
      method: "POST",
      body: JSON.stringify({ text: text ?? null }),
    }),
  postGhCreate: (topicId: number, title: string, body: string) =>
    request<GhCreatePostResult>(`/api/topics/${topicId}/commands/gh-create/post`, {
      method: "POST",
      body: JSON.stringify({ title, body }),
    }),
  draftGhClose: (topicId: number, text?: string) =>
    request<CommentDraft>(`/api/topics/${topicId}/commands/gh-close/draft`, {
      method: "POST",
      body: JSON.stringify({ text: text ?? null }),
    }),
  postGhClose: (
    topicId: number,
    body: string,
    stateReason: "completed" | "not_planned" = "completed",
  ) =>
    request<GhCloseResult>(`/api/topics/${topicId}/commands/gh-close/post`, {
      method: "POST",
      body: JSON.stringify({ body, state_reason: stateReason }),
    }),

  // /notes — freeform note capture
  rephraseNotes: (topicId: number, text: string, instruction?: string) =>
    request<{ text: string }>(
      `/api/topics/${topicId}/commands/notes/rephrase`,
      {
        method: "POST",
        body: JSON.stringify({ text, instruction: instruction ?? null }),
      },
    ),
  appendNotes: (topicId: number, text: string, attachmentIds: number[] = []) =>
    request<{ message: Message }>(
      `/api/topics/${topicId}/commands/notes/append`,
      { method: "POST", body: JSON.stringify({ text, attachment_ids: attachmentIds }) },
    ),
  getNotesDraft: (topicId: number) =>
    request<NotesDraft>(`/api/topics/${topicId}/commands/notes/draft`),
  saveNotesDraft: (topicId: number, text: string) =>
    request<NotesDraft>(`/api/topics/${topicId}/commands/notes/draft`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),
  clearNotesDraft: (topicId: number) =>
    request<void>(`/api/topics/${topicId}/commands/notes/draft`, { method: "DELETE" }),
  listNoteAttachments: (topicId: number) =>
    request<NoteDraftAttachment[]>(`/api/topics/${topicId}/commands/notes/attachments`),
  uploadNoteAttachment: async (topicId: number, file: File): Promise<NoteDraftAttachment> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`/api/topics/${topicId}/commands/notes/attachments`, {
      method: "POST",
      headers: { "X-Client-Id": CLIENT_ID },
      body: form,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return (await res.json()) as NoteDraftAttachment;
  },
  deleteNoteAttachment: (topicId: number, attachmentId: number) =>
    request<void>(`/api/topics/${topicId}/commands/notes/attachments/${attachmentId}`, {
      method: "DELETE",
    }),
  noteAttachmentUrl: (attachmentId: number) => `/api/notes/attachments/${attachmentId}`,

  // Plugins
  listPlugins: () => request<PluginDescriptor[]>(`/api/plugins`),

  // Skills
  listSkills: () => request<Skill[]>(`/api/skills`),
  createSkill: (data: SkillCreate) =>
    request<Skill>(`/api/skills`, { method: "POST", body: JSON.stringify(data) }),
  updateSkill: (id: number, data: SkillUpdate) =>
    request<Skill>(`/api/skills/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteSkill: (id: number) =>
    request<void>(`/api/skills/${id}`, { method: "DELETE" }),
  skillExportUrl: (id: number) => `/api/skills/${id}/export`,

  // Roles (Assistant personas)
  listRoles: () => request<Role[]>(`/api/roles`),
  createRole: (data: RoleCreate) =>
    request<Role>(`/api/roles`, { method: "POST", body: JSON.stringify(data) }),
  updateRole: (id: number, data: RoleUpdate) =>
    request<Role>(`/api/roles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteRole: (id: number) =>
    request<void>(`/api/roles/${id}`, { method: "DELETE" }),

  // Memories
  listMemories: () => request<Memory[]>(`/api/memories`),
  createMemory: (data: MemoryCreate) =>
    request<Memory>(`/api/memories`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateMemory: (id: number, data: MemoryUpdate) =>
    request<Memory>(`/api/memories/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteMemory: (id: number) =>
    request<void>(`/api/memories/${id}`, { method: "DELETE" }),

  // Current user
  getMe: () => request<Me>(`/api/me`),

  // Workspaces
  listWorkspaces: () => request<Workspace[]>(`/api/workspaces`),
  createWorkspace: (data: WorkspaceCreate) =>
    request<Workspace>(`/api/workspaces`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deleteWorkspace: (id: number) =>
    request<void>(`/api/workspaces/${id}`, { method: "DELETE" }),
  updateWorkspace: (id: number, data: WorkspaceUpdate) =>
    request<Workspace>(`/api/workspaces/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  listWorkspaceFiles: (workspaceId: number) =>
    request<WorkspaceFileNode[]>(`/api/workspaces/${workspaceId}/files`),
  readWorkspaceFile: (workspaceId: number, path: string) =>
    request<WorkspaceFileContent>(
      `/api/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
    ),
  writeWorkspaceFile: (workspaceId: number, path: string, content: string) =>
    request<WorkspaceFileContent>(
      `/api/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
      { method: "PUT", body: JSON.stringify({ content }) },
    ),
  createWorkspaceFile: (workspaceId: number, path: string, content = "") =>
    request<WorkspaceFileContent>(`/api/workspaces/${workspaceId}/file`, {
      method: "POST",
      body: JSON.stringify({ path, content }),
    }),
  createWorkspaceFolder: (workspaceId: number, path: string) =>
    request<WorkspaceFileNode>(`/api/workspaces/${workspaceId}/folder`, {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  deleteWorkspaceFile: (workspaceId: number, path: string) =>
    request<void>(
      `/api/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),
  workspaceGitStatus: (workspaceId: number) =>
    request<GitStatus>(`/api/workspaces/${workspaceId}/git/status`),
  workspaceGitPull: (workspaceId: number) =>
    request<GitActionResult>(`/api/workspaces/${workspaceId}/git/pull`, {
      method: "POST",
    }),
  workspaceGitCommitPush: (
    workspaceId: number,
    message: string,
    paths?: string[],
  ) =>
    request<GitActionResult>(`/api/workspaces/${workspaceId}/git/commit-push`, {
      method: "POST",
      body: JSON.stringify(paths ? { message, paths } : { message }),
    }),
  workspaceGitDiscard: (workspaceId: number, path: string) =>
    request<GitStatus>(
      `/api/workspaces/${workspaceId}/git/discard?path=${encodeURIComponent(path)}`,
      { method: "POST" },
    ),
  workspaceGitDiff: (workspaceId: number, path: string) =>
    request<FileDiff>(
      `/api/workspaces/${workspaceId}/git/diff?path=${encodeURIComponent(path)}`,
    ),
  workspaceLocalPath: (workspaceId: number) =>
    request<LocalPath>(`/api/workspaces/${workspaceId}/local-path`),

  // Usage statistics
  getUsageStats: () => request<UsageStats>(`/api/stats/usage`),
};

/** URL that serves a workspace file's raw bytes (static-web-server style). */
export function workspaceRawUrl(slug: string, path: string): string {
  const encoded = path
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return `/raw/${encodeURIComponent(slug)}/${encoded}`;
}
