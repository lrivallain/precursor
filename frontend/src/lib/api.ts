import type {
  GitHubIssue,
  MCPServerStatus,
  Message,
  PluginDescriptor,
  Settings,
  SettingsUpdate,
  Topic,
  TopicNode,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
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
  createTopic: (data: Partial<Topic>) =>
    request<Topic>(`/api/topics`, { method: "POST", body: JSON.stringify(data) }),
  updateTopic: (id: number, data: Partial<Topic>) =>
    request<Topic>(`/api/topics/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteTopic: (id: number) => request<void>(`/api/topics/${id}`, { method: "DELETE" }),

  // Messages
  listMessages: (topicId: number) =>
    request<Message[]>(`/api/topics/${topicId}/messages`),

  // Settings
  getSettings: () => request<Settings>(`/api/settings`),
  updateSettings: (data: SettingsUpdate) =>
    request<Settings>(`/api/settings`, { method: "PUT", body: JSON.stringify(data) }),

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

  // Plugins
  listPlugins: () => request<PluginDescriptor[]>(`/api/plugins`),
};
