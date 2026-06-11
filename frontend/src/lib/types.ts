export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface Topic {
  id: number;
  title: string;
  description: string | null;
  parent_id: number | null;
  github_repo: string | null;
  github_issue_number: number | null;
  created_at: string;
  updated_at: string;
}

export interface TopicNode extends Topic {
  children: TopicNode[];
}

export interface Message {
  id: number;
  topic_id: number;
  role: MessageRole;
  content: string;
  tool_calls: string | null;
  created_at: string;
}

export interface Settings {
  theme: "light" | "dark" | "system";
  llm_model: string;
  github_repo: string;
  mcp_enabled: Record<string, boolean>;
  mcp_servers: Record<string, Record<string, unknown>>;
  api_keys_present: Record<string, boolean>;
}

export interface SettingsUpdate {
  theme?: Settings["theme"];
  llm_model?: string;
  github_repo?: string;
  mcp_enabled?: Record<string, boolean>;
  mcp_servers?: Record<string, Record<string, unknown>>;
  api_keys?: Record<string, string>;
}

export interface GitHubIssue {
  number: number;
  title: string;
  state: string;
  url: string;
  body: string;
  labels: string[];
  updated_at: string;
}

export interface MCPServerStatus {
  name: string;
  transport: string;
  command: string | null;
  url: string | null;
  state: "disconnected" | "connecting" | "connected" | "error";
  error: string | null;
  tools: string[];
}

export interface PluginDescriptor {
  id: string;
  kind: string;
  slot: string;
  title: string;
  config: Record<string, unknown>;
}
