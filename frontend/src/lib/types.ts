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
  // Collection the topic belongs to. Membership cascades down the tree, so a
  // subtree is never split across collections.
  collection_id: number | null;
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
  | "waiting"
  | "running"
  | "idle"
  | "needs_approval"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

// What set an execution going (mirrors backend AGENT_RUN_TRIGGERS).
export type AgentRunTrigger =
  | "manual"
  | "workflow"
  | "schedule"
  | "webhook"
  | "fleet"
  | "retry"
  | "replay";

// One execution of an agent (mirrors backend AgentRunRead).
//
// The agent row is the *definition*; a run is one execution of it. Two workflows
// driving the same agent get a run each, which is what keeps their status,
// capability snapshot, artifacts, token spend and SDK session separate.
export interface AgentRun {
  id: number;
  agent_id: number;
  trigger: AgentRunTrigger;
  status: AgentStatus;
  // Which pipeline attempt drove this run, when a workflow did.
  workflow_run_id: number | null;
  workflow_run_step_id: number | null;
  active_prompt: string | null;
  blocked_question: string | null;
  result_summary: string | null;
  error: string | null;
  step_count: number;
  progress: number | null;
  progress_label: string | null;
  // Spend for *this execution only*. The agent's own totals stay cumulative.
  total_input_tokens: number;
  total_output_tokens: number;
  // The capability snapshot taken when the run opened — editing the agent
  // mid-run does not change what the run is already executing with.
  model: string | null;
  use_mcp: boolean;
  use_skills: boolean;
  use_memory: boolean;
  approval_policy: AgentApprovalPolicy | null;
  role_id: number | null;
  started_at: string | null;
  finished_at: string | null;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentSession {
  id: number;
  public_id: string | null;
  title: string;
  task_prompt: string;
  active_prompt: string | null;
  status: AgentStatus;
  result_summary: string | null;
  error: string | null;
  model: string | null;
  // --- Autonomy / mission state (mirrors backend) ---
  // When on, the agent runs a goal loop toward `task_prompt` (its objective),
  // continuing on its own between turns and pausing only by exception.
  autonomy_enabled: boolean;
  // Max autonomous continuation steps before it hands back to the human.
  max_steps: number;
  // Steps taken toward the current objective run (reset on fresh human intent).
  step_count: number;
  // Agent's self-reported mission progress (0–100) + label; null when unknown.
  progress: number | null;
  progress_label: string | null;
  // When `status === "blocked"`, the question the agent raised for a human.
  blocked_question: string | null;
  // Per-agent approval-policy override; null = inherit the global default.
  approval_policy: AgentApprovalPolicy | null;
  // --- Fleet governance / budgets / retry (mirrors backend) ---
  // Cap on cumulative tokens before the governor parks the agent; null = none.
  token_budget: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
  // Auto-retry budget after a failed turn (0 = off) and attempts used so far.
  max_retries: number;
  retry_count: number;
  // Scheduler due time for the next backoff retry (null when not retrying).
  next_retry_at: string | null;
  // Provenance: the blueprint this agent was stamped from, if any.
  blueprint_id: number | null;
  // When the agent reached a terminal state (completed/failed/cancelled).
  finished_at: string | null;
  topic_id: number | null;
  chat_id: number | null;
  // Assistant Role appended to the agent's system preamble. Null resolves to
  // the default role (no persona).
  role_id: number | null;
  /** True when the agent is a workflow step's private execution vessel. */
  inline: boolean;
  /** What this agent may draw on (all default on). */
  use_mcp: boolean;
  use_skills: boolean;
  use_memory: boolean;
  last_activity_at: string | null;
  archived_at: string | null;
  last_read_at: string | null;
  /** Live (non-archived) workflows that use this agent. */
  workflow_count: number;
  // Assistant replies produced since the user last opened the session
  // (computed server-side; mirrors Chat.unread_count).
  unread_count: number;
  created_at: string;
  updated_at: string;
  // Recurrence config + run state when the agent re-runs on a cadence (null
  // when unscheduled). Mirrors backend AgentScheduleSummary.
  schedule: AgentScheduleSummary | null;
  // Live in-flight activity derived from the manager's in-memory event cache
  // (null/0 when the agent isn't running in-process). Drives the dashboard
  // cockpit's "what is it doing right now" indicators; not persisted.
  active_tool: string | null;
  // Distinct tool calls running in parallel right now — the sub-agent fan-out
  // cluster indicator (>1 means parallel work in flight).
  active_tool_count: number;
  // One-line plain-language hint of what the agent is doing right now, distilled
  // from its own in-flight commentary (null when idle / not live in-process).
  active_narration: string | null;
  // Oldest unresolved permission request blocking the agent (null when not
  // waiting). Deep-links the out-of-band "agent is waiting" signal.
  pending_permission: AgentPendingPermission | null;
  // --- Orchestration relations (eager-loaded by the router) ---
  // The execution currently driving this agent, if any. The execution fields
  // above mirror it so existing surfaces keep working; this is the authoritative
  // record and the handle for per-run artifacts, events and spend.
  current_run: AgentRun | null;
  // External webhook triggers registered on this agent.
  triggers: AgentTrigger[];
  // Published blackboard outputs, newest first.
  artifacts: AgentArtifact[];
}

// An external webhook trigger on an agent (mirrors backend AgentTriggerRead).
export interface AgentTrigger {
  id: number;
  agent_id: number;
  type: string;
  token: string;
  enabled: boolean;
  last_fired_at: string | null;
  created_at: string;
}

// A published blackboard output (mirrors backend AgentArtifactRead).
export interface AgentArtifact {
  id: number;
  agent_id: number;
  // The execution that published this artifact (null for rows written before
  // runs existed, or attached out-of-band).
  agent_run_id: number | null;
  key: string | null;
  kind: string;
  title: string;
  content: string;
  created_at: string;
  updated_at: string;
}

// A durable entry in an agent's private scratchpad (mirrors backend
// AgentStateRead). Unlike an artifact this survives re-runs — it's how a
// scheduled agent remembers a cursor between runs.
export interface AgentState {
  id: number;
  agent_id: number;
  key: string;
  value: string;
  created_at: string;
  updated_at: string;
}

// A durable entry in a workflow's own memory (mirrors backend
// WorkflowStateRead). Shared by every step of the pipeline and kept across
// runs, unlike the per-run trace and the artifact blackboard.
export interface WorkflowState {
  id: number;
  workflow_id: number;
  key: string;
  value: string;
  created_at: string;
  updated_at: string;
}

// The parked approval blocking a live agent (mirrors backend
// AgentPendingPermission).
export interface AgentPendingPermission {
  request_id: string | null;
  title: string | null;
  /** Full request payload, so a non-timeline surface can render the card. */
  data: Record<string, unknown> | null;
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
  role_id?: number | null;
  autonomy_enabled?: boolean;
  max_steps?: number;
  approval_policy?: AgentApprovalPolicy | null;
  // Fleet governance (all optional; omit to leave unset/ungoverned).
  token_budget?: number | null;
  max_retries?: number;
  // Stamp from a blueprint (fields above still override).
  blueprint_id?: number | null;
  // Launch immediately (default). `false` parks it in the `waiting` state until
  // a trigger fires (a parent completing, a webhook, or a manual "Start now").
  start?: boolean;
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
  // Which execution produced this event, so a shared agent's concurrent runs can
  // be read one at a time instead of interleaved.
  agent_run_id: number | null;
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
  // Grants are held per execution, so two concurrent runs of a shared agent
  // never inherit each other's approvals.
  agent_run_id: number | null;
  type: string;
  title: string | null;
  target: string | null;
  at: string | null;
}

export interface AgentLink {
  topic_id?: number | null;
  chat_id?: number | null;
}

// --- Orchestrator: blueprints, inbox, metrics ------------------------------
// A reusable agent template (mirrors backend AgentBlueprintRead).
export interface AgentBlueprint {
  id: number;
  name: string;
  description: string | null;
  task_prompt: string;
  model: string | null;
  role_id: number | null;
  approval_policy: AgentApprovalPolicy | null;
  autonomy_enabled: boolean;
  max_steps: number;
  token_budget: number | null;
  max_retries: number;
  icon: string | null;
  color: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentBlueprintCreate {
  name: string;
  description?: string | null;
  task_prompt?: string;
  model?: string | null;
  role_id?: number | null;
  approval_policy?: AgentApprovalPolicy | null;
  autonomy_enabled?: boolean;
  max_steps?: number;
  token_budget?: number | null;
  max_retries?: number;
  icon?: string | null;
  color?: string | null;
}

export interface AgentBlueprintInstantiate {
  title?: string | null;
  task?: string | null;
  topic_id?: number | null;
  chat_id?: number | null;
  start?: boolean;
}

// Partial update payload for a blueprint (mirrors backend AgentBlueprintUpdate).
export interface AgentBlueprintUpdate {
  name?: string;
  description?: string | null;
  task_prompt?: string;
  model?: string | null;
  role_id?: number | null;
  approval_policy?: AgentApprovalPolicy | null;
  autonomy_enabled?: boolean;
  max_steps?: number;
  token_budget?: number | null;
  max_retries?: number;
  icon?: string | null;
  color?: string | null;
}

// Attach an external trigger to an agent (mirrors backend AgentTriggerCreate).
export interface AgentTriggerCreate {
  type?: "webhook";
  enabled?: boolean;
}

// One thing waiting on a human (mirrors backend AgentInboxItem).
export interface AgentInboxItem {
  agent_id: number;
  title: string;
  kind: "blocked" | "needs_approval" | "budget";
  detail: string | null;
  request_id: string | null;
  at: string | null;
}

export interface AgentStatusCount {
  status: AgentStatus;
  count: number;
}

// Fleet-wide rollup for the dashboard header (mirrors backend AgentMetrics).
export interface AgentMetrics {
  total: number;
  active: number;
  waiting: number;
  completed: number;
  failed: number;
  by_status: AgentStatusCount[];
  total_input_tokens: number;
  total_output_tokens: number;
  running_now: number;
  max_concurrent: number;
}

// A published artifact create payload (mirrors backend AgentArtifactCreate).
export interface AgentArtifactCreate {
  title: string;
  content?: string;
  kind?: "text" | "markdown" | "json" | "link";
  key?: string | null;
}

// --- Workflows (reusable agent-sequence orchestrator) ---
// A workflow owns the *chaining* of otherwise-independent agents. It serialises
// as its ordered steps, each embedding a compact live agent summary. Live
// progress arrives via the `workflow.changed` SSE event. Mirrors the backend
// `schemas/workflow.py`.

export type WorkflowStatus =
  | "draft"
  | "idle"
  | "running"
  | "paused"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

// Just enough of a step's agent to render its node + drive the strip.
export interface WorkflowAgentSummary {
  id: number;
  public_id: string | null;
  title: string;
  status: string;
  /** The agent's objective — lets an inline step be edited from the board. */
  task_prompt: string;
  /** True when this agent is a step's private vessel, not a reusable unit. */
  inline: boolean;
  progress: number | null;
  progress_label: string | null;
  result_summary: string | null;
  active_narration: string | null;
  /** What the agent asked when it parked itself (`status === "blocked"`). */
  blocked_question: string | null;
  /**
   * The tool-permission request parking this step, lifted from the live runtime.
   * The board has to offer the decision: a gate on an *inline* step is
   * otherwise unanswerable, since its vessel is hidden from the Agents roster.
   */
  pending_permission: AgentPendingPermission | null;
  finished_at: string | null;
  updated_at: string;
}

/**
 * What a step *is*. `task` runs a reusable agent you picked; `inline` runs a
 * one-off prompt owned by the step (its agent is hidden and dies with it);
 * `gate` votes PASS/FAIL; `approval` parks the run for a human.
 */
export type WorkflowStepKind = "task" | "inline" | "gate" | "approval";

/** What to do when a step's agent fails or its watchdog fires. */
export type WorkflowStepErrorPolicy = "fail" | "retry" | "continue";

/** What a human rejection at an approval checkpoint does next. */
export type WorkflowStepRejectPolicy = "rework" | "stop" | "skip";

/** How a step sources the context it is handed. */
export type WorkflowStepContextMode = "auto" | "selected" | "none";

export interface WorkflowStep {
  id: number;
  workflow_id: number;
  position: number;
  agent_id: number | null;
  /** Step behaviour: "task" produces, "gate" checks with PASS/FAIL loop-back. */
  kind: WorkflowStepKind;
  /** For a gate: the position to re-drive on FAIL (null = previous step). */
  on_fail_position: number | null;
  /** Per-run re-drive counter, badged in the strip while a gate loops. */
  attempt_count: number;
  /** Extra mandate layered on the agent's objective, for this step only. */
  instructions: string | null;
  /** What to do when this step fails or stalls. */
  on_error: WorkflowStepErrorPolicy;
  max_retries: number;
  retry_count: number;
  /** For an approval step: what a rejection does next. */
  on_reject: WorkflowStepRejectPolicy;
  /** What this step inherits, and from which earlier steps ("0,2"). */
  context_mode: WorkflowStepContextMode;
  context_sources: string | null;
  /** Capability overrides; null = inherit the agent's own setting. */
  use_mcp: boolean | null;
  use_skills: boolean | null;
  use_memory: boolean | null;
  /** Comma-separated MCP server allowlist. Null = every enabled server, an
   *  empty string = none at all (the same as `use_mcp: false`). */
  mcp_servers: string | null;
  /** Optional label override; falls back to the agent's title in the UI. */
  name: string | null;
  /** Embedded live agent state; null when the referenced agent was deleted. */
  agent: WorkflowAgentSummary | null;
}

/** One step *attempt* within a run — the durable trace of what a step saw
 *  (`input_context`) and produced (`output_summary`). Gate loop-backs append a
 *  new attempt row rather than overwriting the prior one. */
export interface WorkflowRunStep {
  id: number;
  run_id: number;
  position: number;
  kind: WorkflowStepKind;
  label: string | null;
  agent_id: number | null;
  /**
   * The specific execution of that agent this attempt drove. Agents are shared,
   * so the attempt's status, spend and artifacts belong to the run, not the
   * agent row — this is the handle that ties the trace to them.
   */
  agent_run_id: number | null;
  /** True when that agent is private to its step, so it isn't in the Agents list. */
  agent_inline: boolean;
  attempt: number;
  /**
   * True when an operator re-ran this step on its own, out of band, on the same
   * input a previous attempt saw. Not a turn the pipeline drove — nothing
   * advanced when it ended.
   */
  replay: boolean;
  status: string;
  input_context: string | null;
  output_summary: string | null;
  gate_verdict: string | null;
  /** Token spend for this attempt (delta across the step's turn). */
  input_tokens: number;
  output_tokens: number;
  started_at: string | null;
  finished_at: string | null;
}

/** One execution of a workflow, with its ordered per-step attempt traces. */
export interface WorkflowRun {
  id: number;
  workflow_id: number;
  run_number: number;
  status: string;
  trigger: string;
  started_at: string | null;
  finished_at: string | null;
  result_summary: string | null;
  error: string | null;
  /** Per-run brief supplied at trigger time; null when run without one. */
  input: string | null;
  /** Cumulative token spend across every attempt in this run. */
  total_input_tokens: number;
  total_output_tokens: number;
  step_runs: WorkflowRunStep[];
}

/** Just enough of a workflow to name and link it from another surface. */
export interface WorkflowSummary {
  id: number;
  name: string;
  icon: string | null;
  status: WorkflowStatus;
}

export interface Workflow {
  id: number;
  name: string;
  description: string | null;
  icon: string | null;
  color: string | null;
  status: WorkflowStatus;
  current_step_id: number | null;
  current_run_id: number | null;
  clear_artifacts: boolean;
  max_loops: number;
  /** Seconds a step may run before the watchdog stops it (null = disabled). */
  step_timeout_seconds: number | null;
  /** Assistant Role applied to every step's agent while the workflow runs. */
  role_id: number | null;
  /**
   * Tool-approval policy applied to every step's agent while it runs; null
   * leaves each agent's own setting alone. The lever that makes an unattended
   * pipeline actually unattended.
   */
  approval_policy: AgentApprovalPolicy | null;
  run_count: number;
  last_run_at: string | null;
  finished_at: string | null;
  result_summary: string | null;
  error: string | null;
  // Scheduling
  schedule_enabled: boolean;
  interval_seconds: number | null;
  run_at_minute: number | null;
  timezone: string;
  days_of_week: number;
  next_run_at: string | null;
  webhook_token: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  steps: WorkflowStep[];
}

// One step in a create/replace payload. Either reference an existing agent by
// `agent_id` OR create one inline from `task` (+ optional title/model).
export interface WorkflowStepInput {
  agent_id?: number | null;
  name?: string | null;
  task?: string | null;
  title?: string | null;
  model?: string | null;
  // For a step that authors its own prompt: true mints a reusable agent listed
  // in the Agents section, false (default) a private vessel owned by the step.
  reusable?: boolean;
  kind?: WorkflowStepKind;
  on_fail_position?: number | null;
  instructions?: string | null;
  on_error?: WorkflowStepErrorPolicy;
  max_retries?: number;
  on_reject?: WorkflowStepRejectPolicy;
  context_mode?: WorkflowStepContextMode;
  context_sources?: string | null;
  use_mcp?: boolean | null;
  use_skills?: boolean | null;
  use_memory?: boolean | null;
  // Null (or omitted) = every enabled server; "" = none at all.
  mcp_servers?: string | null;
}

export interface WorkflowCreate {
  name: string;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  clear_artifacts?: boolean;
  max_loops?: number;
  step_timeout_seconds?: number | null;
  role_id?: number | null;
  approval_policy?: AgentApprovalPolicy | null;
  steps?: WorkflowStepInput[];
}

export interface WorkflowUpdate {
  name?: string | null;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  clear_artifacts?: boolean | null;
  max_loops?: number | null;
  /** Seconds before the stall watchdog fires; 0 disables it. */
  step_timeout_seconds?: number | null;
  /** Assistant Role for the whole workflow; 0 clears it. */
  role_id?: number | null;
  /** Tool-approval policy for every step; null means inherit each agent's own. */
  approval_policy?: AgentApprovalPolicy | null;
  /** When provided, replaces the entire ordered step list. Omit to leave untouched. */
  steps?: WorkflowStepInput[] | null;
}

export interface WorkflowScheduleUpdate {
  schedule_enabled?: boolean | null;
  interval_seconds?: number | null;
  run_at_minute?: number | null;
  timezone?: string | null;
  days_of_week?: number | null;
}

// --- YAML transfer (export / import of agents and workflows) ----------------
// A transfer document is the shareable form of one agent or one workflow: its
// definition, and never its runtime state or per-install secrets. Importing is
// two-phase because the choice below can only be offered once collisions are
// known: `preview` reports them, `import` applies the decisions.

export type TransferKind = "agent" | "workflow";

/** What to do with an incoming object whose name already exists here. */
export type ConflictAction = "replace" | "create" | "link";

export interface TransferConflict {
  kind: "agent" | "workflow";
  /** Index into the document's agent list; null for the workflow itself. */
  index: number | null;
  name: string;
  existing_id: number;
  existing_title: string;
  /** True when this really is the object the file was exported from. */
  same_object: boolean;
  /** Live workflows already using the existing agent — the blast radius of a replace. */
  workflow_count: number;
  allowed: ConflictAction[];
  default: ConflictAction;
}

export interface TransferWarning {
  code: string;
  message: string;
}

export interface TransferPreview {
  kind: TransferKind;
  name: string;
  agent_count: number;
  step_count: number;
  conflicts: TransferConflict[];
  warnings: TransferWarning[];
}

export interface TransferResolution {
  kind: "agent" | "workflow";
  index: number | null;
  action: ConflictAction;
}

export interface TransferImportResult {
  kind: TransferKind;
  workflow_id: number | null;
  agent_id: number | null;
  name: string;
  created_agent_ids: number[];
  replaced_agent_ids: number[];
  linked_agent_ids: number[];
  warnings: TransferWarning[];
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
  /**
   * Client-only: marks a system-role notice as a failure so it renders as an
   * error instead of an acknowledgement. Persisted stream errors are detected
   * from their `Error: ` prefix instead (see lib/systemNotice.ts).
   */
  is_error?: boolean;
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
  // Tenant GUID used by the Agent 365 servers (workiq-teams / workiq-user).
  workiq_tenant_id: string;
  // True when the tenant above was read off a stored token rather than typed.
  workiq_tenant_discovered: boolean;
  // Browser channel the built-in 'playwright' server drives. "default" omits
  // --browser so @playwright/mcp picks its own default.
  playwright_browser: string;
  // System settings (effective: env default with DB override applied).
  llm_max_input_tokens: number;
  llm_max_tool_result_tokens: number;
  scheduled_run_timeout_seconds: number;
  tool_result_retention_days: number;
  live_transcript_retention_days: number;
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
  // Whether the agents manager actually started its runtime in this process.
  // Distinct from agents_available (a stateless capability probe): a degraded
  // runtime reports available=true but runtime_started=false.
  agents_runtime_started: boolean;
  agents_unavailable_reason: string | null;
  agents_default_model: string;
  agents_reasoning_effort: string;
  agents_context_tier: string;
  agents_approval_policy: AgentApprovalPolicy;
  agents_system_prompt: string;
  agents_watchdog_timeout_seconds: number;
  // Workflow defaults: what a new step may draw on, and the stall watchdog a
  // new workflow starts with (0 = off). All overridable per workflow/step.
  workflows_default_use_mcp: boolean;
  workflows_default_use_skills: boolean;
  workflows_default_use_memory: boolean;
  workflows_default_step_timeout_seconds: number;
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
  workiq_tenant_id?: string;
  playwright_browser?: string;
  llm_max_input_tokens?: number;
  llm_max_tool_result_tokens?: number;
  scheduled_run_timeout_seconds?: number;
  tool_result_retention_days?: number;
  live_transcript_retention_days?: number;
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
  workflows_default_use_mcp?: boolean;
  workflows_default_use_skills?: boolean;
  workflows_default_use_memory?: boolean;
  workflows_default_step_timeout_seconds?: number;
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

// --- GitHub Projects v2 (kanban board) ---
// Mirrors precursor.backend.schemas.projects.

export interface ProjectSummary {
  id: string;
  number: number;
  title: string;
  url: string | null;
  closed: boolean;
  short_description: string | null;
}

export interface ProjectColumn {
  id: string;
  name: string;
}

export interface ProjectStatusField {
  id: string;
  name: string;
  options: ProjectColumn[];
}

export interface ProjectCard {
  // ProjectV2 item id — the handle used for status mutations.
  id: string;
  type: "issue" | "pull_request";
  number: number | null;
  title: string;
  url: string | null;
  state: string | null;
  // owner/name of the item's source repo (ProjectsV2 can span repos).
  repo: string | null;
  status_option_id: string | null;
  status_name: string | null;
  labels: IssueLabel[];
}

export interface IssueComment {
  id: number;
  user: string;
  body: string;
  created_at?: string | null;
  updated_at: string;
}

export interface IssueDetail {
  number: number;
  title: string;
  state: string;
  url: string | null;
  body: string;
  labels: IssueLabel[];
  updated_at: string | null;
  comments: IssueComment[];
  linked_topic_id: number | null;
  linked_topic_title: string | null;
}

export interface ProjectBoard {
  id: string;
  title: string;
  url: string | null;
  status_field: ProjectStatusField | null;
  items: ProjectCard[];
}

export interface ItemStatusResult {
  item_id: string;
  option_id: string;
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
  // True when this server signs in through Precursor's browser OAuth flow, so
  // the UI offers the sign-in / re-authenticate action.
  oauth: boolean;
  // Populated for user-defined entries only.
  id: number | null;
  header_keys: string[];
}

/**
 * ``GET /api/mcp/auth/diagnostics`` — everything the backend knows about the
 * WorkIQ OAuth credentials, in one extractable blob.
 *
 * Deliberately loose about ``credentials``/``events`` payloads: this is a
 * diagnostic surface read by humans (via ``window.precursorWorkiqAuthReport()``)
 * rather than a contract the UI renders, so it should keep working when the
 * backend adds a field rather than needing a mirrored edit here.
 */
export interface McpAuthDiagnostics {
  generated_at: string;
  settings: Record<string, unknown>;
  credentials: Record<string, unknown>[];
  events: Record<string, unknown>[];
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
  /** Non-empty => upstream is gone; the text explains what to use instead. */
  retired: string;
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

// Copilot "AI credits" (premium interactions) allowance for the connected
// account. Mirrors the CopilotQuota Pydantic model. `percent_used` drives the
// persona progress bar; `unlimited` plans have nothing to fill.
export interface CopilotQuota {
  plan: string | null;
  unlimited: boolean;
  percent_used: number;
  percent_remaining: number;
  used: number;
  entitlement: number;
  remaining: number;
  reset_date: string | null;
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

// Collection — a named set of topics that filters the sidebar tree. Mirrors the
// backend CollectionRead schema.
export interface Collection {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  // Optional repo override, sitting between the topic's own and the global one.
  github_repo: string | null;
  accent: CollectionAccent;
  icon: string | null;
  // Default Assistant Role for new topics here; null = built-in default role.
  default_role_id: number | null;
  is_default: boolean;
  topic_count: number;
  created_at: string;
  updated_at: string;
}

// Accent keys the SPA maps to static Tailwind classes (see lib/collections.ts).
export type CollectionAccent =
  | "sky"
  | "emerald"
  | "amber"
  | "violet"
  | "rose"
  | "cyan"
  | "slate";

export interface CollectionCreate {
  name: string;
  description?: string | null;
  github_repo?: string | null;
  accent?: CollectionAccent;
  icon?: string | null;
  default_role_id?: number | null;
}

export interface CollectionUpdate {
  name?: string;
  description?: string | null;
  github_repo?: string | null;
  accent?: CollectionAccent;
  icon?: string | null;
  default_role_id?: number | null;
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

/** State of the on-demand draw.io webapp install (schemas/drawio.py). */
export interface DrawioStatus {
  version: string;
  installed: boolean;
  step: "idle" | "download" | "extract";
  downloaded_bytes: number;
  total_bytes: number;
  error: string | null;
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
  // Teams join URL — used to locate the meeting transcript for summaries.
  join_url?: string | null;
  body: string | null;
  body_preview: string | null;
}

export interface AgendaResponse {
  available: boolean;
  events: AgendaEvent[];
  detail: string | null;
}

export interface ExternalMeeting {
  id?: string | null;
  subject: string;
  start?: string | null;
  end?: string | null;
  organizer?: string | null;
  attendees?: AgendaAttendee[];
  is_online?: boolean;
  join_url?: string | null;
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
  // Assistant Role grounding the live assistant (insights/tips, Q&A, chat).
  // Null resolves to the default role (no persona).
  role_id: number | null;
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
  role_id?: number | null;
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

export interface MeetingSegmentUpdate {
  text: string;
}

export interface MeetingInsight {
  id: number;
  session_id: number;
  kind: MeetingInsightKind;
  content: string;
  created_at: string;
}

// ---- Global content search (⌘K palette) --------------------------------
// Mirrors precursor.backend.schemas.search. A flat, ranked list of hits across
// topics, chats, agents (prompts + final answers) and live sessions.
export type SearchSection = "topics" | "chats" | "agents" | "live";

export type SearchField =
  | "title"
  | "description"
  | "message"
  | "prompt"
  | "answer"
  | "transcript"
  | "insight"
  | "notes"
  | "summary";

export interface SearchResult {
  section: SearchSection;
  field: SearchField;
  is_title: boolean;
  entity_id: number;
  // Navigation handle: a topic/chat/live slug or an agent's public id.
  ref: string | null;
  title: string;
  snippet: string;
  role: string | null;
  updated_at: string | null;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
}

// ── Refine with AI ──────────────────────────────────────────────────────────
// Mirrors precursor/backend/schemas/refine.py

export interface RefineRequest {
  text: string;
  // Context hint that tailors the rewrite (e.g. "system_prompt", "note").
  kind?: string;
  // Optional freeform steer ("make it shorter", "more formal").
  instruction?: string;
}

export interface RefineResponse {
  text: string;
  model: string;
}
