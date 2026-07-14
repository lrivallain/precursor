/** Parsed shape of a tool-call message's JSON `tool_calls` payload. */
export interface ParsedToolMeta {
  tool_call_id?: string;
  name?: string;
  arguments?: string;
  is_error?: boolean;
  pending?: boolean;
}

/** Safely parse a message's `tool_calls` JSON blob, returning null on garbage. */
export function parseToolMeta(raw: string | null): ParsedToolMeta | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as ParsedToolMeta;
    return typeof v === "object" && v !== null ? v : null;
  } catch {
    return null;
  }
}
