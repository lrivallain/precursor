import type { MCPServerCreate } from "../../lib/types";

export type McpTransport = "streamable_http" | "stdio";
export interface HeaderRow { key: string; value: string }

export interface McpServerForm {
  name: string;
  transport: McpTransport;
  url: string;
  command: string;
  args: string[];
  headers: HeaderRow[];
}

/**
 * The create/update body for the editor's fields. Only the chosen transport's
 * fields are sent; header rows without a name are dropped.
 */
export function mcpServerPayload(form: McpServerForm): MCPServerCreate {
  const headers: Record<string, string> = {};
  for (const h of form.headers) {
    if (h.key.trim()) headers[h.key.trim()] = h.value;
  }
  return {
    name: form.name.trim(),
    transport: form.transport,
    ...(form.transport === "streamable_http"
      ? { url: form.url.trim(), headers }
      : { command: form.command.trim(), args: form.args }),
  };
}

/** Replace the item at `index`. */
export function replaceAt<T>(items: readonly T[], index: number, next: T): T[] {
  return items.map((item, i) => (i === index ? next : item));
}

/** Drop the item at `index`. */
export function removeAt<T>(items: readonly T[], index: number): T[] {
  return items.filter((_, i) => i !== index);
}
