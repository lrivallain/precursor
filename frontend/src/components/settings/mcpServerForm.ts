import type {
  MCPAuthDetection,
  MCPServerAuth,
  MCPServerCreate,
  MCPServerStatus,
} from "../../lib/types";

export type McpTransport = "streamable_http" | "stdio";
export interface HeaderRow { key: string; value: string }

export interface McpServerForm {
  name: string;
  transport: McpTransport;
  url: string;
  command: string;
  args: string[];
  headers: HeaderRow[];
  auth: MCPServerAuth;
  authTenant: string;
  authResource: string;
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
      ? {
          url: form.url.trim(),
          headers,
          auth: form.auth,
          // Sent even when empty so clearing the tenant falls back to the CLI default.
          auth_tenant: form.auth === "azure_cli" ? form.authTenant.trim() : null,
          auth_resource: form.auth === "azure_cli" ? form.authResource.trim() : null,
        }
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

/** The editor's auth fields for a stored server (or defaults for a new one). */
export function authFormFields(
  server: MCPServerStatus | null,
): Pick<McpServerForm, "auth" | "authTenant" | "authResource"> {
  return {
    auth: server?.auth ?? "none",
    authTenant: server?.auth_tenant ?? "",
    authResource: server?.auth_resource ?? "",
  };
}

/**
 * The form after an auth detection: an Entra server switches to Azure CLI
 * sign-in and fills the tenant and resource; anything else leaves it untouched,
 * so a detection never wipes values the user typed.
 */
export function applyAuthDetection(
  form: McpServerForm,
  detection: MCPAuthDetection,
): Partial<McpServerForm> {
  if (detection.auth !== "azure_cli") return {};
  return {
    auth: "azure_cli",
    authTenant: detection.tenant ?? form.authTenant,
    authResource: detection.resource ?? form.authResource,
  };
}

/** The `az login` that lets the Azure CLI mint a token for this server. */
export function azLoginCommand(tenant: string, resource: string): string {
  const where = tenant.trim() ? ` --tenant ${tenant.trim()}` : "";
  const scope = resource.trim().replace(/\/+$/, "") || "<resource>";
  return `az login${where} --scope ${scope}/.default`;
}
