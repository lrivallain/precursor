import { Plus, RefreshCw } from "lucide-react";
import type { MCPServerStatus, Settings } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { Agent365Card, PlaywrightCard, McpExposeCard } from "./McpCards";
import { McpServerCard } from "./McpServerCard";
import { McpServerEditor } from "./McpServerEditor";
import { signInRole } from "./mcpServerList";
import type { McpSettings } from "./useMcpSettings";

export function McpTab({ s, settings }: { s: McpSettings; settings: Settings | null }) {
  const { servers } = s;
  return (
    <section>
      <McpExposeCard
        expose={s.expose}
        setExpose={s.setExpose}
        precursorEnabled={servers.servers.find((x) => x.name === "precursor")?.enabled ?? false}
        httpEnabled={s.httpEnabled}
        setHttpEnabled={s.setHttpEnabled}
        httpUrl={settings?.mcp_http_url ?? null}
        httpLoopbackOk={settings?.mcp_http_loopback_ok ?? true}
      />
      <Agent365Card
        tenant={s.workiqTenant}
        setTenant={s.setWorkiqTenant}
        discoveredTenant={settings?.workiq_tenant_discovered ? (settings?.workiq_tenant_id ?? "") : ""}
      />
      <PlaywrightCard browser={s.playwrightBrowser} setBrowser={s.setPlaywrightBrowser} />
      <label className="flex items-start gap-2 mb-3 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={s.deferTools}
          onChange={(e) => s.setDeferTools(e.target.checked)}
          className="accent-accent mt-0.5"
        />
        <span>
          <span className="font-medium">Load tools on demand</span>
          <span className="block text-[11px] text-muted">
            Send only a few tool definitions with each request and let
            the assistant load the others by name when it needs them.
            Requests get much smaller and faster, which matters on
            deployments with a low tokens-per-minute quota. Tick
            "always load" on a server to send all its tools every time.
          </span>
        </span>
      </label>
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11px] text-muted">
          Enable an MCP server to expose its tools to the chat. The
          assistant will call them on its own when relevant.
        </p>
        <button
          type="button"
          onClick={() => servers.setEditing("new")}
          className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs whitespace-nowrap"
        >
          <Plus size={12} /> New
        </button>
      </div>
      <ServerList s={s} />
      {servers.editing && (
        <McpServerEditor
          server={servers.editing === "new" ? null : servers.editing}
          onClose={() => servers.setEditing(null)}
          onSaved={async () => {
            await servers.refresh();
            servers.setEditing(null);
          }}
        />
      )}
    </section>
  );
}

function ServerList({ s }: { s: McpSettings }) {
  const { servers } = s;
  const confirmAction = useConfirm();
  if (servers.servers.length === 0) {
    return servers.loading ? (
      <p className="flex items-center gap-2 text-xs text-muted">
        <RefreshCw size={12} className="animate-spin" />
        Loading MCP servers…
      </p>
    ) : (
      <p className="text-xs text-muted">No MCP servers registered.</p>
    );
  }
  const onDelete = async (server: MCPServerStatus): Promise<void> => {
    if (server.id == null) return;
    const ok = await confirmAction({
      message: `Delete MCP server "${server.name}"?`,
      confirmLabel: "Delete MCP server",
      variant: "danger",
    });
    if (ok) await servers.remove(server);
  };
  return (
    <ul className="space-y-2">
      {servers.servers.map((server) => {
        const signIn = signInRole(server, servers.owners);
        return (
          <McpServerCard
            key={server.name}
            server={server}
            onToggle={() => void servers.toggle(server.name, server.enabled)}
            alwaysLoad={s.deferTools ? (s.alwaysLoad[server.name] ?? false) : undefined}
            onToggleAlwaysLoad={() => s.toggleAlwaysLoad(server.name)}
            onReload={server.enabled ? () => void servers.reloadTools(server.name) : undefined}
            onTogglePreview={
              server.preview != null
                ? () => void servers.togglePreview(server.name, server.preview ?? false)
                : undefined
            }
            onReauthenticate={signIn.owner ? () => void servers.reauthenticate(server.name) : undefined}
            sharedSignInWith={signIn.sharedWith}
            onEdit={() => servers.setEditing(server)}
            onDelete={() => onDelete(server)}
          />
        );
      })}
    </ul>
  );
}
