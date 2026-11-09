// Event fixtures shared by the agent timeline tests.
import type { AgentEvent } from "./types";

export function ev(kind: string, fields: Partial<AgentEvent> = {}): AgentEvent {
  return {
    kind,
    text: null,
    tool_name: null,
    tool_status: null,
    request_id: null,
    data: null,
    at: null,
    agent_run_id: null,
    event_id: null,
    link_id: null,
    ...fields,
  };
}

export const user = (text: string, at: string | null = null, extra: Partial<AgentEvent> = {}): AgentEvent =>
  ev("user_message", { text, at, ...extra });

export const assistant = (text: string, at: string | null = null, extra: Partial<AgentEvent> = {}): AgentEvent =>
  ev("assistant_message", { text, at, ...extra });

export const idle = (): AgentEvent => ev("session_idle");
