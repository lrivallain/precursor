export type MessageRole = "user" | "assistant" | "system" | "tool";

// Running app version (CalVer), surfaced by GET /api/version.
export interface AppVersion {
  version: string;
  commit: string | null;
  build_date: string | null;
}

export type TopicKind = "standard";

export interface Topic {
  id: number;
  slug: string;
  title: string;
  description: string | null;
  parent_id: number | null;
  github_repo: string | null;
  github_issue_number: number | null;
  pinned: boolean;
  kind: TopicKind;
  archived_at: string | null;
  role_id: number | null;
  created_at: string;
  updated_at: string;
  // Recurrence summary when the topic runs on a schedule (null otherwise).
  schedule: ScheduleSummary | null;
}

// Lightweight schedule view embedded in the sidebar tree (mirrors backend
// ScheduleSummary). Datetimes are ISO-8601 UTC strings.
export interface ScheduleSummary {
  enabled: boolean;
  interval_seconds: number;
  days_of_week: number;
  run_at_minute: number | null;
  timezone: string;
  clear_context: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  status: string;
}

// Full schedule record (mirrors backend ScheduleRead).
export interface Schedule extends ScheduleSummary {
  id: number;
  topic_id: number;
  prompt: string;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

// Attach a recurrence to an existing topic (no title — the topic owns it).
export interface TopicScheduleCreate {
  prompt: string;
  interval_seconds: number;
  days_of_week?: number;
  run_at_minute?: number | null;
  timezone?: string;
  clear_context?: boolean;
  enabled?: boolean;
}

export interface ScheduleUpdate {
  prompt?: string;
  interval_seconds?: number;
  days_of_week?: number;
  run_at_minute?: number | null;
  timezone?: string;
  clear_context?: boolean;
  enabled?: boolean;
}

export interface TopicNode extends Topic {
  children: TopicNode[];
  unread_count: number;
}

// A flat conversation session (no tree hierarchy, no GitHub link). Mirrors the
// backend ChatRead schema.
export interface Chat {
  id: number;
  slug: string;
  title: string;
  description: string | null;
  description_as_system_prompt: boolean;
  pinned: boolean;
  archived_at: string | null;
  last_read_at: string | null;
  unread_count: number;
  role_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface ChatCreate {
  title: string;
  description?: string | null;
  description_as_system_prompt?: boolean;
  pinned?: boolean;
  slug?: string | null;
}

export interface ChatUpdate {
  title?: string;
  description?: string | null;
  description_as_system_prompt?: boolean;
  pinned?: boolean;
  slug?: string | null;
  role_id?: number | null;
}

// One-shot date/time reminder for a topic or chat. Datetimes are ISO-8601 UTC.
export type ReminderContainer = "topic" | "chat";

export interface Reminder {
  id: number;
  topic_id: number | null;
  chat_id: number | null;
  remind_at: string;
  note: string | null;
  status: "scheduled" | "fired";
  fired_at: string | null;
  created_at: string;
  updated_at: string;
}

// A fired reminder enriched with its container's identity (sidebar list).
export interface ReminderItem extends Reminder {
  container: ReminderContainer;
  title: string;
  slug: string;
}

export interface ReminderCreate {
  remind_at: string;
  note?: string | null;
}

// --- Agents mode (Copilot SDK) ---

export type AgentStatus =
  | "pending"
  | "running"
  | "idle"
  | "needs_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface AgentSession {
  id: number;
  copilot_session_id: string | null;
  title: string;
  task_prompt: string;
  active_prompt: string | null;
  status: AgentStatus;
  result_summary: string | null;
  error: string | null;
  model: string | null;
  topic_id: number | null;
  chat_id: number | null;
  last_activity_at: string | null;
  archived_at: string | null;
  last_read_at: string | null;
  // Assistant replies produced since the user last opened the session
  // (computed server-side; mirrors Chat.unread_count).
  unread_count: number;
  created_at: string;
  updated_at: string;
  // Recurrence config + run state when the agent re-runs on a cadence (null
  // when unscheduled). Mirrors backend AgentScheduleSummary.
  schedule: AgentScheduleSummary | null;
}

// Lightweight schedule view embedded in AgentSession (mirrors backend
// AgentScheduleSummary). Datetimes are ISO-8601 UTC strings.
export interface AgentScheduleSummary {
  enabled: boolean;
  interval_seconds: number;
  days_of_week: number;
  run_at_minute: number | null;
  timezone: string;
  clear_context: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  status: string;
}

// Full agent schedule record (mirrors backend AgentScheduleRead).
export interface AgentSchedule extends AgentScheduleSummary {
  id: number;
  agent_session_id: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentScheduleCreate {
  interval_seconds: number;
  days_of_week?: number;
  run_at_minute?: number | null;
  timezone?: string;
  clear_context?: boolean;
  enabled?: boolean;
}

export interface AgentScheduleUpdate {
  interval_seconds?: number;
  days_of_week?: number;
  run_at_minute?: number | null;
  timezone?: string;
  clear_context?: boolean;
  enabled?: boolean;
}

export interface AgentSessionCreate {
  task: string;
  title?: string | null;
  model?: string | null;
  topic_id?: number | null;
  chat_id?: number | null;
}

// A normalised SDK event, shaped for the workflow-step timeline.
export interface AgentEvent {
  kind: string;
  text: string | null;
  tool_name: string | null;
  tool_status: string | null;
  request_id: string | null;
  data: Record<string, unknown> | null;
  at: string | null;
}

export type AgentPermissionDecisionValue = "approve-once" | "approve-always" | "deny";

// A model exposed by the agents runtime, for the default-model picker.
export interface AgentModelInfo {
  id: string;
  name: string;
  context_window?: number | null;
  supported_reasoning_efforts?: string[];
}

// An active "approve for session" grant, shown in the Settings security recap.
export interface AgentPermissionGrant {
  agent_id: number;
  type: string;
  title: string | null;
  target: string | null;
  at: string | null;
}

export interface AgentLink {
  topic_id?: number | null;
  chat_id?: number | null;
}

export interface Attachment {
  id: number;
  topic_id?: number | null;
  chat_id?: number | null;
  message_id: number | null;
  mime: string;
  size: number;
  original_filename: string;
  created_at: string;
}

export interface Message {
  id: number;
  topic_id: number | null;
  chat_id?: number | null;
  role: MessageRole;
  content: string;
  tool_calls: string | null;
  agent_session_id?: number | null;
  /** The linked agent's public (UUID) id — used for deep links / the /agent command. */
  agent_session_public_id?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  /** The LLM model id that produced this assistant turn. */
  model?: string | null;
  /** Wall-clock time in ms the assistant turn took to generate. */
  elapsed_ms?: number | null;
  created_at: string;
  attachments?: Attachment[];
  /** Follow-up reply chips the assistant offered on this turn. */
  suggestions?: string[];
}

export interface NotesDraft {
  text: string | null;
  updated_at: string | null;
  attachments: NoteDraftAttachment[];
}

export interface NoteDraftAttachment {
  id: number;
  note_draft_id: number;
  mime: string;
  size: number;
  original_filename: string;
  created_at: string;
}

// Default approval policy gating agent actions:
//  - "manual":     ask before every action
//  - "balanced":   auto-approve read-only actions, ask for the rest
//  - "autonomous": auto-approve everything
export type AgentApprovalPolicy = "manual" | "balanced" | "autonomous";

export interface Settings {
  theme: "light" | "dark" | "system";
  llm_model: string;
  // "" => auto/off; otherwise "low" | "medium" | "high".
  llm_reasoning_effort: string;
  github_repo: string;
  issue_context_ttl_minutes: number;
  show_chat_stats: boolean;
  notifications_enabled: boolean;
  max_tool_rounds: number;
  mcp_enabled: Record<string, boolean>;
  mcp_servers: Record<string, Record<string, unknown>>;
  api_keys_present: Record<string, boolean>;
  github_token_source: "env" | "gh-cli" | "settings" | "none";
  issue_associations_enabled: boolean;
  // Active LLM provider id + per-provider public config (secrets redacted) and
  // a per-provider secret-presence map.
  llm_provider: string;
  llm_providers: Record<string, Record<string, string>>;
  llm_providers_present: Record<string, Record<string, boolean>>;
  // Azure AI Speech: configured endpoint + language + readiness (key never echoed).
  azure_speech_endpoint: string;
  azure_speech_language: string;
  stt_azure_ready: boolean;
  // Live meeting assistant: enablement + model + reasoning effort for fast
  // analysis / Q&A. Empty model resolves to the default chat model.
  live_enabled: boolean;
  live_fast_model: string;
  live_reasoning_effort: string;
  // Which Precursor capability sections the built-in MCP server exposes.
  mcp_expose: Record<string, boolean>;
  // HTTP transport for the built-in 'precursor' MCP server.
  mcp_http_enabled: boolean;
  mcp_http_url: string | null;
  mcp_http_loopback_ok: boolean;
  // System settings (effective: env default with DB override applied).
  llm_max_input_tokens: number;
  llm_max_tool_result_tokens: number;
  scheduled_run_timeout_seconds: number;
  tool_result_retention_days: number;
  cmd_runner_jail: boolean;
  cmd_runner_image: string;
  cmd_runner_network: boolean;
  cmd_runner_timeout_seconds: number;
  cmd_runner_max_output_bytes: number;
  cmd_runner_memory: string;
  cmd_runner_pids_limit: number;
  cmd_runner_cpus: string;
  docker_available: boolean;
  // Agents mode (Copilot SDK): the enabled preference, whether the runtime is
  // usable right now (probe), an optional reason when it isn't, and the default
  // model for new agent sessions.
  agents_enabled: boolean;
  agents_available: boolean;
  agents_unavailable_reason: string | null;
  agents_default_model: string;
  agents_reasoning_effort: string;
  agents_context_tier: string;
  agents_approval_policy: AgentApprovalPolicy;
  agents_system_prompt: string;
  agents_watchdog_timeout_seconds: number;
  // Folder backup (DB + blobs) into a user-picked directory, e.g. a
  // OneDrive-synced folder. Last-run fields are read-only status.
  backup_enabled: boolean;
  backup_dir: string;
  backup_retention: number;
  backup_last_run_at: string | null;
  backup_last_status: string | null;
  backup_last_error: string | null;
}

export interface SettingsUpdate {
  theme?: Settings["theme"];
  llm_model?: string;
  llm_reasoning_effort?: string;
  github_repo?: string;
  issue_context_ttl_minutes?: number;
  show_chat_stats?: boolean;
  notifications_enabled?: boolean;
  max_tool_rounds?: number;
  mcp_enabled?: Record<string, boolean>;
  mcp_servers?: Record<string, Record<string, unknown>>;
  api_keys?: Record<string, string>;
  issue_associations_enabled?: boolean;
  llm_provider?: string;
  llm_providers?: Record<string, Record<string, string>>;
  azure_speech_endpoint?: string;
  azure_speech_language?: string;
  live_enabled?: boolean;
  live_fast_model?: string;
  live_reasoning_effort?: string;
  mcp_expose?: Record<string, boolean>;
  mcp_http_enabled?: boolean;
  llm_max_input_tokens?: number;
  llm_max_tool_result_tokens?: number;
  scheduled_run_timeout_seconds?: number;
  tool_result_retention_days?: number;
  cmd_runner_jail?: boolean;
  cmd_runner_image?: string;
  cmd_runner_network?: boolean;
  cmd_runner_timeout_seconds?: number;
  cmd_runner_max_output_bytes?: number;
  cmd_runner_memory?: string;
  cmd_runner_pids_limit?: number;
  cmd_runner_cpus?: string;
  agents_enabled?: boolean;
  agents_default_model?: string;
  agents_reasoning_effort?: string;
  agents_context_tier?: string;
  agents_approval_policy?: AgentApprovalPolicy;
  agents_system_prompt?: string;
  agents_watchdog_timeout_seconds?: number;
  backup_enabled?: boolean;
  backup_dir?: string;
  backup_retention?: number;
}

// Outcome of a manual "Back up now" run (POST /api/settings/backup/run).
export interface BackupRunResult {
  ok: boolean;
  // "ok" | "skipped" | "error"
  status: string;
  detail: string;
  db_snapshot: string | null;
  // Files newly copied this run.
  blobs_copied: number;
  // Total blob files present in the destination mirror after the run.
  blobs_total: number;
}

export interface IssueLabel {
  name: string;
  color: string;
}

export interface GitHubIssue {
  number: number;
  title: string;
  state: string;
  url: string;
  body: string;
  labels: IssueLabel[];
  updated_at: string;
}

export interface GitHubProject {
  id: string;
  number: number;
  title: string;
  url: string;
}

export interface KanbanColumnOption {
  id: string;
  name: string;
  color: string;
}

export interface KanbanIssue extends GitHubIssue {
  item_id: string;
  column_id: string | null;
}

export interface KanbanBoard {
  project_id: string;
  field_id: string | null;
  columns: KanbanColumnOption[];
  issues: KanbanIssue[];
}

export interface MCPTool {
  name: string;
  description: string;
}

export interface MCPServerStatus {
  name: string;
  transport: string;
  command: string | null;
  command_bin: string | null;
  args: string[];
  url: string | null;
  state:
    | "disconnected"
    | "connecting"
    | "connected"
    | "ready"
    | "error"
    | "needs_auth"
    | "disabled";
  error: string | null;
  tools: MCPTool[];
  builtin: boolean;
  enabled: boolean;
  // Workiq-only: hosted HTTP + OAuth (writes) when true, local stdio when false.
  // null for servers where preview mode does not apply.
  preview: boolean | null;
  // Populated for user-defined entries only.
  id: number | null;
  header_keys: string[];
}

export interface MCPServerCreate {
  name: string;
  transport: "streamable_http" | "stdio";
  url?: string | null;
  command?: string | null;
  args?: string[];
  headers?: Record<string, string>;
}

export interface MCPServerUpdate {
  name?: string;
  transport?: "streamable_http" | "stdio";
  url?: string | null;
  command?: string | null;
  args?: string[];
  headers?: Record<string, string>;
}

export interface LLMModel {
  id: string;
  name: string;
  publisher: string;
  summary: string;
  tags: string[];
  context_window?: number | null;
  /** Reasoning-effort values this model accepts, ascending. Empty when the
   *  model isn't reasoning-capable (the composer hides the effort picker). */
  supported_reasoning_efforts?: string[];
}

export interface LLMProviderField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  placeholder: string;
  help: string;
}

export interface LLMProviderSpec {
  id: string;
  label: string;
  fields: LLMProviderField[];
  uses_github_token: boolean;
  discovers_models: boolean;
}

export interface IssueSummary {
  repo: string;
  issue_number: number;
  issue_title: string;
  issue_state: string;
  issue_url: string | null;
  labels: IssueLabel[];
  summary: string;
  model: string;
  fetched_at: string;
  cached: boolean;
}

export interface IssuePushResult {
  repo: string;
  issue_number: number;
  issue_title: string;
  issue_state: string;
  issue_url: string | null;
}

export interface CommentDraft {
  draft: string;
  source: "user" | "llm";
  repo: string;
  issue_number: number;
}

export interface CommentPostResult {
  repo: string;
  issue_number: number;
  comment_url: string | null;
  message: Message;
  note_upload_failures: string[];
  local_note_message: Message | null;
}

export interface GhSyncResult {
  repo: string;
  issue_number: number;
  issue_state: string;
  issue_title: string;
  message: Message;
}

export interface GhCreateDraft {
  title: string;
  body: string;
  repo: string;
  source: string;
}

export interface GhCreatePostResult {
  repo: string;
  issue_number: number;
  issue_url: string | null;
  issue_title: string;
  message: Message;
}

export interface GhCloseResult {
  repo: string;
  issue_number: number;
  issue_state: string;
  comment_url: string | null;
  message: Message;
}

export interface PluginDescriptor {
  id: string;
  kind: string;
  slot: string;
  title: string;
  config: Record<string, unknown>;
}

export interface GitHubIdentity {
  login: string;
  name: string | null;
  avatar_url: string | null;
  html_url: string | null;
}

export interface Me {
  github: GitHubIdentity | null;
  github_token_source: "env" | "gh-cli" | "settings" | "none";
}

export interface Skill {
  name: string;
  description: string | null;
  instructions: string;
  /** File-backed skill is turned on (or legacy skill, always on until migrated). */
  enabled: boolean;
  /** Usable as a slash command right now. */
  active: boolean;
  /** Un-migrated DB skill — exposes a "Migrate" action. */
  legacy: boolean;
}

export interface SkillCreate {
  name: string;
  description?: string | null;
  instructions: string;
}

export interface SkillUpdate {
  name?: string;
  description?: string | null;
  instructions?: string;
  enabled?: boolean;
}

// Assistant Role — a persistent persona (system prompt) attached to a
// discussion. Mirrors the backend RoleRead schema.
export interface Role {
  id: number;
  name: string;
  system_prompt: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface RoleCreate {
  name: string;
  system_prompt?: string;
}

export interface RoleUpdate {
  name?: string;
  system_prompt?: string;
}

export interface Memory {
  id: number;
  kind: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryCreate {
  kind: string;
  content: string;
}

export interface MemoryUpdate {
  kind?: string;
  content?: string;
}

export type WorkspaceKind = "git" | "local";

export interface Workspace {
  id: number;
  name: string;
  slug: string;
  kind: WorkspaceKind;
  repo_url: string | null;
  branch: string;
  subdir: string | null;
  cloned_at: string | null;
  last_synced_at: string | null;
  role_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceCreate {
  name: string;
  kind?: WorkspaceKind;
  repo_url?: string | null;
  branch?: string;
  subdir?: string | null;
  slug?: string | null;
}

export interface WorkspaceUpdate {
  role_id?: number | null;
}

export interface WorkspaceFileNode {
  path: string;
  name: string;
  type: "file" | "dir";
}

export interface WorkspaceFileContent {
  path: string;
  content: string;
}

export interface GitFileStatus {
  path: string;
  code: string;
}

export interface GitStatus {
  branch: string;
  ahead: number | null;
  behind: number | null;
  dirty: boolean;
  files: GitFileStatus[];
}

export interface GitActionResult {
  ok: boolean;
  detail: string;
  needs_manual_merge: boolean;
  local_path: string | null;
  status: GitStatus | null;
}

export interface FileDiff {
  path: string;
  diff: string;
  binary: boolean;
}

export interface LocalPath {
  path: string;
}

export interface UsageBucket {
  period: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  message_count: number;
}

export interface UsageStats {
  totals: UsageBucket;
  weekly: UsageBucket[];
  monthly: UsageBucket[];
  yearly: UsageBucket[];
}

export interface TableStat {
  name: string;
  row_count: number;
  size_bytes: number | null;
}

export interface DatabaseStats {
  engine: string;
  size_bytes: number | null;
  path: string | null;
  tables: TableStat[];
}

export interface BlobStats {
  count: number;
  size_bytes: number;
  path: string | null;
}

export interface IssueStats {
  configured: boolean;
  repo: string | null;
  open: number | null;
  closed: number | null;
  error: string | null;
}

export interface EntityCounts {
  topics: number;
  chats: number;
  agents: number;
  workspaces: number;
}

export interface SystemStats {
  database: DatabaseStats;
  blobs: BlobStats;
  issues: IssueStats;
  entities: EntityCounts;
}

export interface WorkspaceChatMessage {
  role: "user" | "assistant";
  content: string;
}

// ---- Live meeting assistant --------------------------------------------
export type MeetingStatus = "active" | "ended";

export interface AgendaAttendee {
  name: string;
  email: string | null;
}

export interface AgendaEvent {
  id: string | null;
  subject: string;
  start: string | null;
  end: string | null;
  organizer: string | null;
  attendees: AgendaAttendee[];
  is_online: boolean;
  body: string | null;
  body_preview: string | null;
}

export interface AgendaResponse {
  available: boolean;
  events: AgendaEvent[];
  detail: string | null;
}

export interface ExternalMeeting {
  subject: string;
  start?: string | null;
  end?: string | null;
  organizer?: string | null;
  attendees?: AgendaAttendee[];
  is_online?: boolean;
  body?: string | null;
  body_preview?: string | null;
}

export type MeetingInsightKind =
  | "action_item"
  | "decision"
  | "question"
  | "suggestion"
  | "risk"
  | "note";

export interface MeetingSession {
  id: number;
  title: string;
  slug: string;
  status: MeetingStatus;
  language: string | null;
  topic_id: number | null;
  // Chat spawned for the "Ask assistant" tab (created on first ask), or null.
  chat_id: number | null;
  // Map of raw diarization label (e.g. "Guest-2") -> chosen display name.
  speaker_names: Record<string, string>;
  // Attendee display names used in the summary (editable).
  attendees: string[];
  // Free-form notes pinned to the grounding context (e.g. saved Q&A answers).
  context_notes: string[];
  // Live Markdown notes the user takes during the meeting.
  notes: string;
  // Enabled optional Live features (insights, notes, assistant, proactive, translation).
  features: string[];
  // A linked M365 calendar meeting (subject/times/attendees), or null.
  external_meeting: ExternalMeeting | null;
  // Cached AI summary of the attached topic (Context tab). Generated once and
  // persisted; regenerated only on explicit refresh or when the topic changes.
  topic_summary: string | null;
  // The generated meeting recap (Summary tab), persisted so a reopened session
  // shows it without regenerating. Replaced only on explicit Regenerate.
  summary: string | null;
  // When the recap was last posted to a topic, and which topic it landed in.
  // Null until the first successful post.
  summary_posted_at: string | null;
  summary_posted_topic_id: number | null;
  started_at: string | null;
  ended_at: string | null;
  // When non-null, the session is archived (hidden from the Live list).
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MeetingSessionCreate {
  title?: string | null;
  language?: string | null;
  topic_id?: number | null;
  slug?: string | null;
}

export interface MeetingSessionUpdate {
  title?: string | null;
  language?: string | null;
  topic_id?: number | null;
  status?: MeetingStatus;
  notes?: string | null;
  features?: string[];
}

export interface MeetingAttachment {
  id: number;
  mime: string;
  original_filename: string;
  url: string;
  is_image: boolean;
}

export interface MeetingSegment {
  id: number;
  session_id: number;
  speaker_label: string | null;
  text: string;
  offset_ms: number | null;
  created_at: string;
}

export interface MeetingSegmentCreate {
  text: string;
  speaker_label?: string | null;
  offset_ms?: number | null;
}

export interface MeetingInsight {
  id: number;
  session_id: number;
  kind: MeetingInsightKind;
  content: string;
  created_at: string;
}
