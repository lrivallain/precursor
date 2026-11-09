import type { Settings } from "../../lib/types";

// The editable "System" settings subset of the full Settings payload.
export interface SystemSettings {
  llm_max_input_tokens: number;
  llm_max_tool_result_tokens: number;
  llm_tool_result_keep_turns: number;
  llm_max_attachment_chars: number;
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
}

export function pickSystem(s: Settings): SystemSettings {
  return {
    llm_max_input_tokens: s.llm_max_input_tokens,
    llm_max_tool_result_tokens: s.llm_max_tool_result_tokens,
    llm_tool_result_keep_turns: s.llm_tool_result_keep_turns,
    llm_max_attachment_chars: s.llm_max_attachment_chars,
    scheduled_run_timeout_seconds: s.scheduled_run_timeout_seconds,
    tool_result_retention_days: s.tool_result_retention_days,
    cmd_runner_jail: s.cmd_runner_jail,
    cmd_runner_image: s.cmd_runner_image,
    cmd_runner_network: s.cmd_runner_network,
    cmd_runner_timeout_seconds: s.cmd_runner_timeout_seconds,
    cmd_runner_max_output_bytes: s.cmd_runner_max_output_bytes,
    cmd_runner_memory: s.cmd_runner_memory,
    cmd_runner_pids_limit: s.cmd_runner_pids_limit,
    cmd_runner_cpus: s.cmd_runner_cpus,
  };
}
