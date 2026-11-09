// Shapes the API exchanges come from the backend's Pydantic schemas, generated
// into ./generated/apiTypes.ts (`make api-types`). Alias them here
// (`export type X = Api.X;`) instead of re-declaring them; only frontend-only
// types should be written by hand below. A field that is too loose in the
// generated type (e.g. `string` instead of a union) is fixed in the backend
// schema with a `Literal`, not here.
import type * as Api from "./generated/apiTypes";

export type MessageRole = Api.MessageRole;

// Running app version (CalVer), surfaced by GET /api/version.
export interface AppVersion {
  version: string;
  commit: string | null;
  build_date: string | null;
}

export type TopicKind = "standard";

export type ConversationLLMSelection = Api.ConversationLLMSelection;

export interface Topic {
  llm_selection: ConversationLLMSelection | null;
  id: number;
  slug: string;
  // Immutable UUID permalink (`/topics/id/<public_id>`). Unlike the readable URL it
  // never changes when the topic is renamed, re-parented or moved collection.
  public_id: string;
  title: string;
  description: string | null;
  parent_id: number | null;
  github_repo: string | null;
  github_issue_number: number | null;
  pinned: boolean;
  kind: TopicKind;
  archived_at: string | null;
  role: string | null;
  // Collection the topic belongs to. Membership cascades down the tree, so a
  // subtree is never split across collections.
  collection_id: number | null;
  created_at: string;
  updated_at: string;
  // Recurrence summary when the topic runs on a schedule (null otherwise).
  schedule: ScheduleSummary | null;
  /** Quoted reply the conversation started from (a promoted side chat's). */
  seed_content?: string | null;
  /** Null inherits the global default (`topic_approval_policy` / `chat_approval_policy`). */
  approval_policy?: ConversationApprovalPolicy | null;
}

// One "when to run" clause of a schedule (mirrors backend RecurrenceRule). A
// schedule fires at the earliest next occurrence across all of its rules, so
// "every day at 07:00" + "every weekday at 12:00" gives both.
export type RecurrenceRule = Api.RecurrenceRule;

// Lightweight schedule view embedded in the sidebar tree (mirrors backend
// ScheduleSummary). Datetimes are ISO-8601 UTC strings.
export type ScheduleSummary = Api.ScheduleSummary;

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
// Send either the flat fields or `rules`; `rules` wins when both are present.
export type TopicScheduleCreate = Api.TopicScheduleCreate;

export type ScheduleUpdate = Api.ScheduleUpdate;

export interface TopicNode extends Topic {
  children: TopicNode[];
  unread_count: number;
  /** Tool calls waiting for the user's approval. */
  pending_approvals?: number;
}

// A flat conversation session (no tree hierarchy, no GitHub link). Mirrors the
// backend ChatRead schema.
export interface Chat {
  llm_selection: ConversationLLMSelection | null;
  id: number;
  public_id: string;
  slug: string;
  title: string;
  description: string | null;
  description_as_system_prompt: boolean;
  pinned: boolean;
  archived_at: string | null;
  last_read_at: string | null;
  unread_count: number;
  role: string | null;
  created_at: string;
  updated_at: string;
  /** Side chat: the topic it was started from (null once that topic is deleted). */
  parent_topic_id?: number | null;
  parent_topic_title?: string | null;
  /** The reply it was started from, while that reply still exists. */
  parent_message_id?: number | null;
  /** Frozen copy of that reply; survives its rewind or deletion. */
  seed_content?: string | null;
  approval_policy?: ConversationApprovalPolicy | null;
  /** The delegated plan's default for this discussion, if it has one. */
  inherited_approval_policy?: ConversationApprovalPolicy | null;
  inherited_approval_plan_id?: number | null;
  pending_approvals?: number;
}

// A side chat as listed in its parent topic's right panel. Mirrors the
// backend SideChatItem schema.
export type SideChatItem = Api.SideChatItem;

// A side chat's outcome, drafted for its parent topic. Mirrors TopicNoteDraft.
export type TopicNoteDraft = Api.TopicNoteDraft;

export type ChatCreate = Api.ChatCreate;

export type ChatUpdate = Api.ChatUpdate;

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
export type ReminderItem = Api.ReminderItem;

export type ReminderCreate = Api.ReminderCreate;

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
  /** The server allowlist this run executes under (see `AgentSession`). */
  mcp_servers: string | null;
  approval_policy: AgentApprovalPolicy | null;
  role: string | null;
  /** Files mode: the definition file this run was declared by, and its SHA-256. */
  definition_path?: string | null;
  definition_hash?: string | null;
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
  role: string | null;
  /** True when the agent is a workflow step's private execution vessel. */
  inline: boolean;
  /** What this agent may draw on (all default on). */
  use_mcp: boolean;
  use_skills: boolean;
  use_memory: boolean;
  /** Which MCP servers it may see. Null = every enabled server (the default),
   *  `"a,b"` = only those, `""` = none at all. A workflow step's own scope
   *  still overrides this for the duration of that step. */
  mcp_servers: string | null;
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
  // Tail of the thinking the model is streaming right now (null when it isn't
  // thinking); cards preview its latest step.
  active_thinking: string | null;
  // Oldest unresolved permission request blocking the agent (null when not
  // waiting). Deep-links the out-of-band "agent is waiting" signal.
  pending_permission: AgentPendingPermission | null;
  // --- Orchestration relations (eager-loaded by the router) ---
  // The execution currently driving this agent, if any. The execution fields
  // above mirror it so existing surfaces keep working; this is the authoritative
  // record and the handle for per-run artifacts, events and spend.
  current_run: AgentRun | null;
  /** Files mode only: which file declares this agent (null in database mode). */
  definition?: DefinitionSource | null;
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
export type AgentPendingPermission = Api.AgentPendingPermission;

// Lightweight schedule view embedded in AgentSession (mirrors backend
// AgentScheduleSummary). Datetimes are ISO-8601 UTC strings.
export type AgentScheduleSummary = Api.AgentScheduleSummary;

// Full agent schedule record (mirrors backend AgentScheduleRead).
export interface AgentSchedule extends AgentScheduleSummary {
  id: number;
  agent_session_id: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

// Send either the flat fields or `rules`; `rules` wins when both are present.
export type AgentScheduleCreate = Api.AgentScheduleCreate;

export type AgentScheduleUpdate = Api.AgentScheduleUpdate;

export type AgentSessionCreate = Api.AgentSessionCreate;

// A normalised SDK event, shaped for the workflow-step timeline.
export type AgentEvent = Api.AgentEvent;

// One incremental read of an agent's transcript (mirrors AgentEventPage).
// The archived timeline is append-only, so a live view sends back the previous
// `cursor` as `after` and receives only what it hasn't seen.
// The model's thinking for the round in flight, streamed but not yet archived.
// Mirrors backend AgentLiveThinking.
export type AgentLiveThinking = Api.AgentLiveThinking;

export type AgentEventPage = Api.AgentEventPage;

export type AgentRewindMode = "conversation" | "conversation-and-files";

/** Mirrors `AgentRewindSkippedFile` in `schemas/agent.py`. */
export type AgentRewindSkippedFile = Api.AgentRewindSkippedFile;

/** Mirrors `AgentRewindResult` in `schemas/agent.py`. */
export type AgentRewindResult = Api.AgentRewindResult;

/** Mirrors `AgentRewindFile` in `schemas/agent.py`. */
export type AgentRewindFile = Api.AgentRewindFile;

/** Mirrors `AgentRewindPreview` in `schemas/agent.py`. */
export type AgentRewindPreview = Api.AgentRewindPreview;

export type AgentPermissionDecisionValue = "approve-once" | "approve-always" | "deny";

// A model exposed by the agents runtime, for the default-model picker.
export type AgentModelInfo = Api.AgentModelInfo;

// An active "approve for session" grant, shown in the Settings security recap.
export type AgentPermissionGrant = Api.AgentPermissionGrant;

// An in-flight (or last finished) Copilot CLI provisioning attempt.
export type AgentProvisionJob = Api.AgentProvisionJob;

// Everything Settings → Agents needs to explain — and fix — the runtime.
// Answers "why not, and what can I do about it", not just "is it on".
export type AgentRuntimeStatus = Api.AgentRuntimeStatus;

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
  role: string | null;
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

export type AgentBlueprintCreate = Api.AgentBlueprintCreate;

export type AgentBlueprintInstantiate = Api.AgentBlueprintInstantiate;

// Partial update payload for a blueprint (mirrors backend AgentBlueprintUpdate).
export type AgentBlueprintUpdate = Api.AgentBlueprintUpdate;

// Attach an external trigger to an agent (mirrors backend AgentTriggerCreate).
export type AgentTriggerCreate = Api.AgentTriggerCreate;

// One thing waiting on a human (mirrors backend AgentInboxItem).
export type AgentInboxItem = Api.AgentInboxItem;

export type AgentStatusCount = Api.AgentStatusCount;

// Fleet-wide rollup for the dashboard header (mirrors backend AgentMetrics).
export type AgentMetrics = Api.AgentMetrics;

// A published artifact create payload (mirrors backend AgentArtifactCreate).
export type AgentArtifactCreate = Api.AgentArtifactCreate;

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
export type WorkflowAgentSummary = Api.WorkflowAgentSummary;

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
  /** Files mode: the workflow file this run executed, and its SHA-256 then. */
  definition_path?: string | null;
  definition_hash?: string | null;
  step_runs: WorkflowRunStep[];
}

/**
 * How far a workflow's newest run has advanced — served with the workflow so
 * the gallery can follow several live runs at once without fetching a run per
 * card. Null until the workflow has ever run. Mirrors the backend
 * `WorkflowRunProgress`.
 */
export type WorkflowRunProgress = Api.WorkflowRunProgress;

/** Just enough of a workflow to name and link it from another surface. */
export type WorkflowSummary = Api.WorkflowSummary;

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
  role: string | null;
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
  // Scheduling. The flat fields hold the primary rule; `rules` is the full set.
  schedule_enabled: boolean;
  interval_seconds: number | null;
  run_at_minute: number | null;
  timezone: string;
  days_of_week: number;
  rules: RecurrenceRule[];
  next_run_at: string | null;
  webhook_token: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  steps: WorkflowStep[];
  /** Advancement of the newest run; null until the workflow has ever run. */
  run_progress: WorkflowRunProgress | null;
  /** Files mode only: which file declares this workflow (null in database mode). */
  definition?: DefinitionSource | null;
}

// One step in a create/replace payload. Either reference an existing agent by
// `agent_id` OR create one inline from `task` (+ optional title/model).
export type WorkflowStepInput = Api.WorkflowStepInput;

export type WorkflowCreate = Api.WorkflowCreate;

export type WorkflowUpdate = Api.WorkflowUpdate;

export type WorkflowScheduleUpdate = Api.WorkflowScheduleUpdate;

// --- YAML transfer (export / import of agents and workflows) ----------------
// A transfer document is the shareable form of one agent or one workflow: its
// definition, and never its runtime state or per-install secrets. Importing is
// two-phase because the choice below can only be offered once collisions are
// known: `preview` reports them, `import` applies the decisions.

export type TransferKind = "agent" | "workflow";

/** What to do with an incoming object whose name already exists here. */
export type ConflictAction = "replace" | "create" | "link";

export type TransferConflict = Api.TransferConflict;

export type TransferWarning = Api.TransferWarning;

export type TransferPreview = Api.TransferPreview;

export type TransferResolution = Api.TransferResolution;

export type TransferImportResult = Api.TransferImportResult;

// --- Definition files (work in progress) ------------------------------------
// Agents and workflows declared in *.agent.yaml / *.workflow.yaml files, with
// only execution data left in the database. Mirrors schemas/definitions_api.py;
// see docs/definitions.md. They declare agents and workflows only in files mode.

export type DefinitionKind = "agent" | "workflow";
/** What the migration wizard lists: roles are files too. */
export type MigrationKind = DefinitionKind | "role";
/** What a file in the definitions folder can declare: summary templates have no row. */
export type DefinitionFileKind = DefinitionKind | "summary" | "role";

/**
 * Where an agent's or workflow's declaration comes from, in files mode:
 * `file` = declared by `path`; `invalid` = `path` has errors, so it can't run;
 * `none` = no file carries its id, so it still runs from the database.
 */
export type DefinitionSource = Api.DefinitionSource;

export type DefinitionIssue = Api.DefinitionIssue;

export type DefinitionFileSummary = Api.DefinitionFileSummary;

export type DefinitionsDatabaseLinks = Api.DefinitionsDatabaseLinks;

export type DefinitionsCheckReport = Api.DefinitionsCheckReport;

export type ExportedDefinition = Api.ExportedDefinition;

/** What the check says about one file opened in the Files section. */
export type DefinitionFileReport = Api.DefinitionFileReport;

// --- Migration (database → files, and back) ---------------------------------

/** Cheap summary for the Agents and Workflows homes: anything left to migrate? */
export type DefinitionsStatus = Api.DefinitionsStatus;

export type DefinitionsWorkspaceRef = Api.DefinitionsWorkspaceRef;

export type MigrationAction = "create" | "regenerate" | "unchanged" | "new_from_disk";

export type MigrationItem = Api.MigrationItem;

export type MigrationRecord = Api.MigrationRecord;

export type FinalizedRecord = Api.FinalizedRecord;

export interface CleanupPreview {
  ready: boolean;
  blockers: string[];
  warnings: string[];
  agents: number;
  step_prompts: number;
  workflows: number;
  steps: number;
  /** Roles whose prompt the cleanup blanks in the database (the files hold them). */
  roles?: number;
  /** Agents and workflows without a file yet; the cleanup writes them first. */
  missing_files: string[];
}

export interface MigrationPreview {
  /** database: not migrated · files: migrated, can switch back · finalized: done, no way back. */
  stage: "database" | "files" | "finalized";
  migrated: MigrationRecord | null;
  finalized: FinalizedRecord | null;
  cleanup: CleanupPreview | null;
  source: "database" | "files";
  /** PRECURSOR_DEFINITIONS_SOURCE=files forces files mode. */
  forced: boolean;
  folder: string;
  workspace: DefinitionsWorkspaceRef | null;
  /** A definition file in the folder, for an "Open in Files" link. */
  sample_path: string | null;
  items: MigrationItem[];
  /** Roles that get a file (database stage) or already have one. */
  roles?: number;
  issues: DefinitionIssue[];
  blockers: string[];
  ready: boolean;
  needs_confirmation: boolean;
}

export type MigrationResult = Api.MigrationResult;

/** The actual content behind one migration preview line. */
export type MigrationItemDetail = Api.MigrationItemDetail;

export type FinalizeResult = Api.FinalizeResult;

export type RevertResult = Api.RevertResult;

export type DefinitionsExportResult = Api.DefinitionsExportResult;

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
  /** Absent on local-only notices and optimistic streaming rows. */
  public_id?: string;
  topic_id: number | null;
  chat_id?: number | null;
  role: MessageRole;
  content: string;
  /** Edited assistant replies: the model's answer before the edit (null when untouched). */
  original_content?: string | null;
  /** When the reply was last edited (null when untouched). */
  edited_at?: string | null;
  /** Special row or Step-by-Step drafting-prompt origin. */
  kind?: string | null;
  /** Assistant turns: the model's thinking for this round, shown collapsed. */
  reasoning?: string | null;
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
  provider?: string | null;
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

/**
 * What a user-stopped turn leaves behind (`POST …/messages/stopped`), mirroring
 * `schemas/message.py`: the reply text received so far, and the ids of the
 * latest tool round's calls that never returned (recorded as stopped). At least
 * one of the two must be present. `reasoning` is the thinking streamed for that
 * reply, stored with it.
 */
/**
 * Estimated history the next turn sends to the model, plus the system prompt
 * and tool definitions it carries. `tokens` counts the history alone. Mirrors
 * `ContextEstimateRead` in `schemas/message.py`.
 */
export interface ContextEstimate {
  tokens: number;
  by_role: Record<string, number>;
  /** Tokens saved by stripping inline binaries and eliding stale tool results. */
  saved_by_hygiene: number;
  messages: number;
  compacted: boolean;
  compaction_id: number | null;
  /** Rows the latest compaction marker hides from the model. */
  compacted_messages: number;
  system_tokens: number;
  /** Tool definitions of Precursor's own MCP server. */
  system_tools_tokens: number;
  /** Tool definitions of every other enabled MCP server. */
  mcp_tools_tokens: number;
  /** The latest model request: total input and its prompt-cache share (0 when unknown). */
  last_prompt_tokens?: number;
  last_cache_read_tokens?: number;
  last_cache_write_tokens?: number;
}

export type StoppedTurn = Api.StoppedTurn;

/**
 * One turn of the transcript timeline: a user prompt and every row up to the
 * next one. Mirrors `TurnIndexRead` in `schemas/message.py`.
 */
export interface TurnIndexItem {
  message_id: number;
  last_message_id: number;
  created_at: string;
  /** Excerpt (≤ 200 chars) of the prompt. */
  prompt: string;
  /** Excerpt of the turn's first assistant text; empty while unanswered. */
  reply: string;
  has_compaction: boolean;
  agent_session_id: number | null;
}

/** Mirrors `RewindResult` in `schemas/message.py`. */
export type RewindResult = Api.RewindResult;

/**
 * The editable status brief shown above a topic's transcript. Mirrors
 * `schemas/topic_summary.py`. A topic has none until one is generated or an
 * item is added to it, so the API resolves to `null` rather than 404.
 */
export interface TopicSummary {
  revision: string;
  content: string;
  visible: boolean;
  user_edited: boolean;
  model: string | null;
  generated_at: string | null;
  updated_at: string | null;
  suggestion: TopicSummarySuggestion | null;
}

/** A model proposal parked for per-change review on a user-edited summary. */
export interface TopicSummarySuggestion {
  content: string;
  model: string | null;
  generated_at: string | null;
  hunks: TopicSummaryHunk[];
}

/**
 * One contiguous change (removed lines → added lines) in a proposal.
 * `base_start`/`base_end` are line offsets into the *current* content, so the
 * change can be rendered inline at its real position rather than in a
 * detached list.
 */
export interface TopicSummaryHunk {
  index: number;
  base_start: number;
  base_end: number;
  removed: string[];
  added: string[];
}

/** Omit reviewed to resolve the entire proposal; otherwise decide only those hunks. */
export type TopicSummaryResolve = Api.TopicSummaryResolve;

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

/** Per-conversation tool approval policy (mirrors backend `ApprovalPolicy`). */
export type ConversationApprovalPolicy = AgentApprovalPolicy | "plan";

/** A tool call (or the model's own intent) waiting on, or settled by, the user. */
export interface ToolApproval {
  id: number;
  topic_id: number | null;
  chat_id: number | null;
  assistant_message_id: number | null;
  tool_call_id: string;
  tool_name: string;
  server: string;
  raw_tool: string;
  arguments: string;
  kind: "tool" | "intent";
  reason: string | null;
  status: "pending" | "approved" | "denied" | "cancelled";
  scope: "once" | "conversation" | "global";
  feedback: string | null;
  /** Decided and its result already recorded in the transcript. */
  executed: boolean;
  created_at: string | null;
  decided_at: string | null;
  /** UTC deadline after which an unattended run's parked call is auto-denied. */
  expires_at: string | null;
}

export type ToolApprovalDecision = Api.ToolApprovalDecision;

/** One condition on a tool call's arguments; all clauses of a rule must hold. */
export type ArgClause = Api.ArgClause;

export type ApprovalRuleEffect = "allow" | "ask";
export type ApprovalRuleScope = "global" | "topic" | "chat";

/** A standing allow / ask answer in front of a conversation's approval policy. */
export interface ApprovalRule {
  id: number;
  effect: ApprovalRuleEffect;
  server: string;
  tool: string;
  arg_match: ArgClause[];
  scope: ApprovalRuleScope;
  topic_id: number | null;
  chat_id: number | null;
  enabled: boolean;
  note: string | null;
  hit_count: number;
  last_used_at: string | null;
  created_from_approval_id: number | null;
  created_at: string | null;
}

export type ApprovalRuleCreate = Api.ApprovalRuleCreate;

export type ApprovalRuleUpdate = Api.ApprovalRuleUpdate;

export type ApprovalDecisionResult = Api.ApprovalDecisionResult;

export type ModelPreset = Api.ModelPreset;

export type ModelCategory = string;
export type ModelCategoryDef = Api.ModelCategoryDef;
export type ModelCategories = Api.ModelCategories;

export interface Settings {
  theme: "light" | "dark" | "system";
  font_family: "system" | "opendyslexic" | "atkinson-hyperlegible" | "lexend";
  llm_model: string;
  llm_model_category: ModelCategory | null;
  llm_category_preset: ModelPreset | null;
  // "" => auto/off; otherwise "low" | "medium" | "high".
  llm_reasoning_effort: string;
  github_repo: string;
  issue_context_ttl_minutes: number;
  show_chat_stats: boolean;
  show_quick_actions: boolean;
  notifications_enabled: boolean;
  completion_sound_enabled: boolean;
  max_tool_rounds: number;
  mcp_enabled: Record<string, boolean>;
  mcp_servers: Record<string, Record<string, unknown>>;
  api_keys_present: Record<string, boolean>;
  github_token_source: "env" | "gh-cli" | "settings" | "none";
  issue_associations_enabled: boolean;
  // Active LLM provider id + per-provider public config (secrets redacted) and
  // a per-provider secret-presence map.
  llm_provider: string;
  model_fallbacks: ModelCategories;
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
  // Auto-naming a placeholder-titled chat from its first message. Empty model
  // resolves to the default chat model.
  chat_autoname_enabled: boolean;
  mock_provider_enabled: boolean;
  chat_autoname_model: string;
  // Which Precursor capability sections the built-in MCP server exposes.
  mcp_expose: Record<string, boolean>;
  // On-demand MCP tool loading and the servers whose tools are always sent.
  mcp_defer_tools: boolean;
  mcp_always_load: Record<string, boolean>;
  // HTTP transport for the built-in 'precursor' MCP server.
  mcp_http_enabled: boolean;
  plugin_install_enabled: boolean;
  mcp_http_url: string | null;
  mcp_http_loopback_ok: boolean;
  // OpenAI-compatible endpoint: the switch, the base URL clients are pointed
  // at and its API key (returned in clear on purpose; "" until first enabled).
  openai_proxy_enabled: boolean;
  openai_proxy_url: string;
  openai_proxy_key: string;
  // Whether the active provider can back the endpoint, and why not.
  openai_proxy_available: boolean;
  openai_proxy_unavailable_reason: string | null;
  // Precursor IQ: opt-in embeddings (spend provider quota), the embeddings
  // model/deployment, and the model writing cited answers ("" = chat model).
  iq_embeddings_enabled: boolean;
  iq_embedding_model: string;
  iq_ask_model: string;
  // Provider serving each ("" = the default provider from Settings → Model).
  iq_embedding_provider: string;
  iq_ask_provider: string;
  // Tenant GUID used by the Agent 365 servers (the workiq-* family).
  workiq_tenant_id: string;
  // True when the tenant above was read off a stored token rather than typed.
  workiq_tenant_discovered: boolean;
  // Browser channel the built-in 'playwright' server drives. "default" omits
  // --browser so @playwright/mcp picks its own default.
  playwright_browser: string;
  // System settings (effective: env default with DB override applied).
  llm_max_input_tokens: number;
  llm_max_tool_result_tokens: number;
  llm_tool_result_keep_turns: number;
  llm_max_attachment_chars: number;
  scheduled_run_timeout_seconds: number;
  tool_result_retention_days: number;
  live_transcript_retention_days: number;
  agent_event_retention_days: number;
  agent_event_max_per_session: number;
  workflow_step_retention_days: number;
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
  agents_model_category: ModelCategory | null;
  agents_reasoning_effort: string;
  agents_context_tier: string;
  /** New agent sessions capture file changes so a rewind can restore them. */
  agents_file_change_tracking: boolean;
  agents_approval_policy: AgentApprovalPolicy;
  /** Default tool approval policy for topics and chats without their own. */
  topic_approval_policy: ConversationApprovalPolicy;
  chat_approval_policy: ConversationApprovalPolicy;
  /** Default minutes before an approval parked by a scheduled run is auto-denied; 0 never. */
  scheduled_approval_timeout_minutes: number;
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
  font_family?: Settings["font_family"];
  llm_model?: string;
  llm_model_category?: ModelCategory | null;
  llm_reasoning_effort?: string;
  github_repo?: string;
  issue_context_ttl_minutes?: number;
  show_chat_stats?: boolean;
  show_quick_actions?: boolean;
  notifications_enabled?: boolean;
  completion_sound_enabled?: boolean;
  max_tool_rounds?: number;
  mcp_enabled?: Record<string, boolean>;
  mcp_servers?: Record<string, Record<string, unknown>>;
  api_keys?: Record<string, string>;
  issue_associations_enabled?: boolean;
  llm_provider?: string;
  model_fallbacks?: ModelCategories;
  llm_providers?: Record<string, Record<string, string>>;
  azure_speech_endpoint?: string;
  azure_speech_language?: string;
  live_enabled?: boolean;
  live_fast_model?: string;
  live_reasoning_effort?: string;
  chat_autoname_enabled?: boolean;
  mock_provider_enabled?: boolean;
  chat_autoname_model?: string;
  mcp_expose?: Record<string, boolean>;
  mcp_defer_tools?: boolean;
  mcp_always_load?: Record<string, boolean>;
  mcp_http_enabled?: boolean;
  plugin_install_enabled?: boolean;
  openai_proxy_enabled?: boolean;
  iq_embeddings_enabled?: boolean;
  iq_embedding_model?: string;
  iq_ask_model?: string;
  iq_embedding_provider?: string;
  iq_ask_provider?: string;
  workiq_tenant_id?: string;
  playwright_browser?: string;
  llm_max_input_tokens?: number;
  llm_max_tool_result_tokens?: number;
  llm_tool_result_keep_turns?: number;
  llm_max_attachment_chars?: number;
  scheduled_run_timeout_seconds?: number;
  tool_result_retention_days?: number;
  live_transcript_retention_days?: number;
  agent_event_retention_days?: number;
  agent_event_max_per_session?: number;
  workflow_step_retention_days?: number;
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
  agents_model_category?: ModelCategory | null;
  agents_reasoning_effort?: string;
  agents_context_tier?: string;
  agents_file_change_tracking?: boolean;
  agents_approval_policy?: AgentApprovalPolicy;
  topic_approval_policy?: ConversationApprovalPolicy;
  chat_approval_policy?: ConversationApprovalPolicy;
  scheduled_approval_timeout_minutes?: number;
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
  // Dated zip of the definitions folder written this run, if it exists.
  definitions_archive: string | null;
}

export type IssueLabel = Api.IssueLabel;

export interface GitHubIssue {
  number: number;
  title: string;
  state: string;
  url: string;
  body: string;
  labels: IssueLabel[];
  updated_at: string;
}

// The GitHub issue read models below mirror precursor.backend.schemas.issues.
// The Projects v2 board models live with the kanban plugin, in its own
// repository (github.com/lrivallain/precursor-kanban).

export type IssueComment = Api.IssueComment;

export type IssueDetail = Api.IssueDetail;

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
  // True when ``tools`` was restored from Precursor's persisted catalogue rather
  // than listed by a live session, so the list is shown but the server is not
  // claimed to be connected.
  tools_from_cache: boolean;
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
  /** Largest reply the model can produce; null when not advertised. */
  max_output_tokens?: number | null;
  /** Whether the model accepts image input. */
  vision?: boolean;
  /** Actual catalogue source; may be "mock" if provider config is unusable. */
  catalog_provider?: string | null;
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
  /** Whether the OpenAI-compatible endpoint can relay to this provider. */
  openai_proxy: boolean;
  /** Non-empty => upstream is gone; the text explains what to use instead. */
}

export type IssueSummary = Api.IssueSummary;

export type IssuePushResult = Api.IssuePushResult;

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

/**
 * A contribution a backend plugin published via `GET /api/plugins`.
 * Mirrors precursor.backend.plugins.registry.FrontendExtension.
 */
export interface PluginDescriptor {
  id: string;
  kind: string;
  slot: string;
  title: string;
  /** Plugin-defined payload. Sections use `order`; the `id` is the URL segment. */
  config: Record<string, unknown>;
  /** Which plugin contributed this. */
  plugin_id: string;
  /**
   * URL of the plugin's built ES module, when it ships one. The SPA imports it
   * before resolving the descriptor, so an out-of-tree plugin can register its
   * own React components. `null` for plugins bundled into the core SPA.
   */
  entry: string | null;
}

/** How a plugin can be installed into this instance. */
export interface PluginEnvironment {
  /** "uv-tool" | "uv-venv" | "pip" — which installer owns the environment. */
  installer: string;
  /** The command a user can run themselves, with `<package>` to substitute. */
  command_template: string;
  python: string;
  /** Whether the server may run the install itself right now. */
  can_install: boolean;
  /** Whether it *could*, if the user opted in — drives the opt-in checkbox. */
  installable_here: boolean;
  reason: string | null;
  /** False where the process can't restart itself; the UI asks for a manual restart. */
  restart_supported: boolean;
}

/** Result of installing or uninstalling a plugin package. */
export interface PluginInstallResult {
  /** The requirement that was installed, e.g. `precursor-kanban==2026.9.1`. */
  package: string;
  /** The concrete version it resolves to, when known up front. */
  version: string | null;
  output: string;
  restart_required: boolean;
  /**
   * The nightly Precursor itself moved to, when the build it was installed
   * from is no longer published and had to be replaced; `null` otherwise.
   */
  host_upgrade: string | null;
}

/** An installed plugin as reported by `GET /api/plugins/installed`. */
export interface InstalledPlugin {
  id: string;
  distribution: string | null;
  version: string | null;
  summary: string | null;
  homepage: string | null;
  enabled: boolean;
  /** Non-null when the plugin's `register()` raised; it can't be enabled. */
  error: string | null;
  entry: string | null;
  sections: Array<{ id: string; title: string }>;
  extensions: Array<{ id: string; kind: string; slot: string; title: string }>;
  /** Route prefixes the plugin mounted, e.g. `/api/github/projects`. */
  routes: string[];
  mcp_servers: Array<{ name: string; title: string }>;
  /** Where upgrades come from; `null` when the distribution is unknown. */
  source: PluginSource | null;
}

/**
 * How a plugin is (or would be) installed. `direct` is a path or an arbitrary
 * URL, which has no list of releases to upgrade from.
 */
export interface PluginSource {
  kind: "pypi" | "github" | "direct";
  distribution: string | null;
  /** `owner/repo` for a GitHub source. */
  repository: string | null;
  /** The constraint the install asked for, e.g. `==1.2` or `>=1.2`. */
  specifier: string;
  /** True when that constraint is an exact `==` pin. */
  pinned: boolean;
}

/** One installable release, with the requirement that would install it. */
export interface PluginRelease {
  version: string;
  prerelease: boolean;
  published_at: string | null;
  tag: string | null;
  requirement: string;
}

/** `GET /api/plugins/versions` — every release of a name or GitHub repository. */
export interface PluginVersions {
  source: PluginSource;
  latest: string | null;
  /** What "Latest" installs: unpinned from PyPI, the newest wheel from GitHub. */
  latest_requirement: string;
  /** Newest first. */
  versions: PluginRelease[];
}

/** `GET /api/plugins/updates` — one row per installed plugin. */
export interface PluginUpdate {
  id: string;
  distribution: string | null;
  installed_version: string | null;
  source: PluginSource;
  latest: string | null;
  update_available: boolean;
  /** What an upgrade to `latest` installs, for running it by hand. */
  upgrade_requirement: string | null;
  /** Why the newest release couldn't be looked up, if it couldn't. */
  error: string | null;
}

/**
 * An entry in the plugin catalogue (`GET /api/plugins/catalog`).
 *
 * The catalogue is bundled with Precursor rather than fetched, so it works
 * offline and every entry was reviewed before it shipped. `distribution` is
 * validated server-side to be a bare PyPI name — it is the only value ever
 * handed to an installer.
 */
export interface CatalogPlugin {
  /** Entry-point name; matches `InstalledPlugin.id` once installed. */
  id: string;
  distribution: string;
  title: string;
  summary: string;
  homepage: string | null;
  /** GitHub repository whose releases are an alternative install source. */
  repository: string | null;
  author: string | null;
  license: string | null;
  tags: string[];
  /** Which of "section" | "settings" | "mcp" | "api" the plugin brings. */
  contributes: string[];
  /** Featured entries lead the list. */
  recommended: boolean;
  /** Path to this entry's page in the bundled docs site. */
  docs_path: string;
  installed: boolean;
  enabled: boolean;
  installed_version: string | null;
}

export type GitHubIdentity = Api.GitHubIdentity;

export type Me = Api.Me;

// Copilot "AI credits" (premium interactions) allowance for the connected
// account. Mirrors the CopilotQuota Pydantic model. `percent_used` drives the
// persona progress bar; `unlimited` plans have nothing to fill.
export type CopilotQuota = Api.CopilotQuota;

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

export type SkillCreate = Api.SkillCreate;

export type SkillUpdate = Api.SkillUpdate;

// Assistant Role — a persistent persona (system prompt) attached to a
// discussion. Mirrors the backend RoleRead schema.
export interface Role {
  // A role is identified by its name: there is no id, and conversations store the name.
  name: string;
  system_prompt: string;
  is_default: boolean;
  description?: string | null;
  // The role file, relative to the definitions folder; null for a role held in the database.
  path?: string | null;
}

export type RoleCreate = Api.RoleCreate;

export type RoleUpdate = Api.RoleUpdate;

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
  default_role: string | null;
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

export type CollectionCreate = Api.CollectionCreate;

export type CollectionUpdate = Api.CollectionUpdate;

export interface Memory {
  id: number;
  kind: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export type MemoryCreate = Api.MemoryCreate;

export type MemoryUpdate = Api.MemoryUpdate;

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
  /** Holds the definition files (the built-in "Definitions" workspace). */
  hosts_definitions?: boolean;
  created_at: string;
  updated_at: string;
}

export type WorkspaceCreate = Api.WorkspaceCreate;

export interface WorkspaceFileNode {
  path: string;
  name: string;
  type: "file" | "dir";
}

export interface WorkspaceFileContent {
  path: string;
  content: string;
}

export type GitFileStatus = Api.GitFileStatus;

export type GitStatus = Api.GitStatus;

export type GitActionResult = Api.GitActionResult;

export type FileDiff = Api.FileDiff;

/** A conflicted file's three versions (null where that side has none). */
export type GitConflict = Api.GitConflict;

export type GitBranch = Api.GitBranch;

export type GitBranches = Api.GitBranches;

export type GitCommit = Api.GitCommit;

export type GitLog = Api.GitLog;

export type GitCommitFile = Api.GitCommitFile;

export type GitCommitDetail = Api.GitCommitDetail;

/** Both sides of one file's diff; null where the file doesn't exist. */
export type FileVersions = Api.FileVersions;

export type LocalPath = Api.LocalPath;

/** State of the on-demand draw.io webapp install (schemas/drawio.py). */
export type DrawioStatus = Api.DrawioStatus;

export type UsageBucket = Api.UsageBucket;

export type UsageStats = Api.UsageStats;

export type TableStat = Api.TableStat;

export type DatabaseStats = Api.DatabaseStats;

export type BlobStats = Api.BlobStats;

export type IssueStats = Api.IssueStats;

export type EntityCounts = Api.EntityCounts;

export type SystemStats = Api.SystemStats;

export type CleanupTargetStat = Api.CleanupTargetStat;

export interface CleanupPreview {
  targets: CleanupTargetStat[];
  total_bytes: number;
}

export type CleanupRunResult = Api.CleanupRunResult;

export type CompactResult = Api.CompactResult;

/** A workspace file attached to a topic or chat. */
export interface ConversationArtifact {
  id: number;
  workspace_id: number;
  workspace_slug: string;
  workspace_name: string;
  path: string;
  /** How it got attached: the assistant wrote it, the user attached it, or a discussion was opened on it. */
  source: "assistant" | "manual" | "opened";
  /** False once the file was renamed or deleted outside the app. */
  exists: boolean;
  created_at: string;
  updated_at: string;
}

/** A topic or chat attaching files of one workspace ("continue discussion"). */
export interface WorkspaceDiscussion {
  kind: "chat" | "topic";
  id: number;
  title: string;
  slug: string;
  /** Topics only: the permalink id used in the workspace URL. */
  public_id: string | null;
  updated_at: string;
  paths: string[];
  /** One of the paths is the file the list was asked about. */
  has_file: boolean;
}

/** Where a turn is held from: the workspace (and file) being viewed. */
export type WorkspaceTurnContext = Api.WorkspaceTurnContext;

// ---- Live meeting assistant --------------------------------------------
export type MeetingStatus = "active" | "ended";

export type AgendaAttendee = Api.AgendaAttendee;

export type AgendaEvent = Api.AgendaEvent;

export type AgendaResponse = Api.AgendaResponse;

/**
 * One Teams transcription session of a linked meeting. Teams produces one per
 * start/stop of transcription ("Partie 1", "Partie 2", …), so a meeting that
 * got cut has several; `ended_at` is absent on older transcripts.
 */
export type MeetingTranscriptPart = Api.MeetingTranscriptPart;

export type MeetingTranscriptListResult = Api.MeetingTranscriptListResult;

/** How to write a recap; an omitted field falls back to the one last used. */
export interface MeetingSummaryOptions {
  /** A summary template's id. */
  template?: string;
  /** A language tag (`fr`); `""` writes in the session's own language. */
  language?: string;
}

export type MeetingSummaryResult = Api.MeetingSummaryResult;

export type MeetingTranscriptSummaryResult = Api.MeetingTranscriptSummaryResult;

export interface SummaryTemplate {
  id: string;
  name: string;
  description: string | null;
  source: "builtin" | "file";
  /** Relative to the definitions folder, for a template read from a file. */
  path: string | null;
  /** A file that replaces the built-in template of the same id. */
  overrides_builtin: boolean;
}

export type SummaryLanguage = Api.SummaryLanguage;

export type SummaryTemplateCatalog = Api.SummaryTemplateCatalog;

export type SummaryTemplateFileResult = Api.SummaryTemplateFileResult;

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
  role: string | null;
  // Chat spawned for the "Ask assistant" tab (created on first ask), or null.
  chat_id: number | null;
  // Map of raw diarization label (e.g. "Guest-2") -> chosen display name.
  speaker_names: Record<string, string>;
  /** Speaker labels the user marked as themselves (never asked a suggestion for). */
  me_labels: string[];
  // Attendee display names used in the summary (editable).
  attendees: string[];
  // Free-form notes pinned to the grounding context (e.g. saved Q&A answers).
  context_notes: string[];
  // Live Markdown notes the user takes during the meeting.
  notes: string;
  // Enabled optional Live features (insights, notes, assistant, answers, translation).
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

export type MeetingSessionCreate = Api.MeetingSessionCreate;

export type MeetingSessionUpdate = Api.MeetingSessionUpdate;

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

export type MeetingSegmentCreate = Api.MeetingSegmentCreate;

export type MeetingSegmentUpdate = Api.MeetingSegmentUpdate;

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

export type SearchResult = Api.SearchResult;

export type SearchResponse = Api.SearchResponse;

// Precursor IQ — mirrors precursor/backend/schemas/iq.py.
export type IQSection = SearchSection | "memory";

export type IQField = SearchField | "brief" | "attachment" | "memory";

export type IQHit = Api.IQHit;

export type IQRetrieveResponse = Api.IQRetrieveResponse;

export type IQAskResponse = Api.IQAskResponse;

export type IQStatus = Api.IQStatus;

export type IQEmbeddingTestResponse = Api.IQEmbeddingTestResponse;

// ── Refine with AI ──────────────────────────────────────────────────────────
// Mirrors precursor/backend/schemas/refine.py

export type RefineRequest = Api.RefineRequest;

export type RefineResponse = Api.RefineResponse;

// ── Step by step ────────────────────────────────────────────────────────────
// Mirrors precursor/backend/schemas/step_plan.py

export interface StepPlanTraceData {
  kind: "step_plan_trace";
  version: 1;
  plan_id: number;
  title: string;
  prompt: string;
  source: "submitted" | "topic_message" | "chat_message";
}

export interface StepPlanAmendmentTraceData {
  kind: "step_plan_amendment";
  version: 1;
  plan_id: number;
  title: string;
  revision: number;
  prompt: string;
}

export type StepPlanAmendment = Api.StepPlanAmendment;

export type StepPlanStatus = "drafting" | "active" | "consolidating" | "done" | "cancelled";

export type StepStatus =
  | "proposed"
  | "open"
  | "in_progress"
  | "to_validate"
  | "validated"
  | "cancelled";

/** Statuses a user can set directly (a `proposed` step is accepted or dismissed). */
export type SettableStepStatus = Exclude<StepStatus, "proposed">;

/** Who drives the work: the user, the model drafts for review, or a full model run. */
export type AutomationMode = "manual" | "assisted" | "auto";
export type AutoState = "idle" | "running" | "paused";

/** One fact the app measured itself for an assessment (a file exists, a slide count). */
export type AssessmentCheck = Api.AssessmentCheck;

/** A model's self-assessment of a result. Not a calibrated probability. */
export type Assessment = Api.Assessment;

export interface Step {
  id: number;
  plan_id: number;
  parent_step_id: number | null;
  position: number;
  title: string;
  instruction: string;
  status: StepStatus;
  /** Roll-up for groups (derived from their children); equals `status` for leaves. */
  effective_status: StepStatus;
  artifact: string;
  artifact_version: number;
  /** A validated artifact whose ask or instruction changed: re-review it. */
  needs_review: boolean;
  assessment: Assessment | null;
  /** The artifact, instruction or ask changed since it was assessed. */
  assessment_stale: boolean;
  created_by: "user" | "model";
  chat_id: number | null;
  chat_slug: string | null;
  created_at: string;
  updated_at: string;
}

/** One line of the plan's activity feed (a step trace written by the run). */
export type ActivityNote = Api.ActivityNote;

export interface StepPlan {
  id: number;
  title: string;
  source_text: string;
  status: StepPlanStatus;
  archived_at: string | null;
  source_topic_id: number | null;
  source_topic_title: string | null;
  source_chat_id: number | null;
  source_chat_title: string | null;
  role: string | null;
  llm_selection: ConversationLLMSelection | null;
  output_instructions: string | null;
  amendments: StepPlanAmendment[];
  state_token: string;
  final_output: string | null;
  /** Which version of the final output this is (0: none yet). */
  final_version: number;
  /** The latest version filed into the topic, if any. */
  final_posted_version: number | null;
  final_posted_at: string | null;
  final_stale: boolean;
  /** The final output was written before every step was validated. */
  final_provisional: boolean;
  automation_mode: AutomationMode;
  delegation_policy: "manual" | "balanced" | "autonomous" | "plan" | null;
  auto_state: AutoState;
  /** The step being drafted right now, only while the run is going. */
  auto_step_id: number | null;
  /** The latest run traces, oldest first. */
  activity: ActivityNote[];
  assessment: Assessment | null;
  assessment_stale: boolean;
  ready_to_consolidate: boolean;
  consolidation_chat_id: number | null;
  consolidation_chat_slug: string | null;
  steps: Step[];
  created_at: string;
  updated_at: string;
}

export type StepPlanItem = Api.StepPlanItem;

export type StepPlanCreate = Api.StepPlanCreate;

export type StepPlanUpdate = Api.StepPlanUpdate;

export type StepPlanAmendRequest = Api.StepPlanAmendRequest;

export type StepCreate = Api.StepCreate;

export type StepUpdate = Api.StepUpdate;

export type StepMove = Api.StepMove;

export interface StepArtifactVersion {
  version: number;
  content: string;
  author: "user" | "model";
  created_at: string;
}

export interface FinalVersion extends StepArtifactVersion {
  /** Set when this version was filed into the topic. */
  posted_at: string | null;
  posted_message_id: number | null;
}

/** What a model draft turned into: saved as a version, kept in the discussion, or paused. */
export type DraftOutcome = "published" | "discussion" | "awaiting_approval";

export type ArtifactDraftResult = Api.ArtifactDraftResult;

export type FinalDraftResult = Api.FinalDraftResult;

export type StepPlanReview = Api.StepPlanReview;
