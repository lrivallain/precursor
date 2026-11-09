import { Plus, RefreshCw } from "lucide-react";
import { api } from "../../lib/api";
import { mcpAuthFamily, mcpServerLabel } from "../../lib/mcpServers";
import { Agent365Card, PlaywrightCard, McpExposeCard, McpServerCard } from "./McpCards";
import { McpServerEditor } from "./McpServerEditor";
import type { SettingsPanelState } from "./useSettingsPanelState";

export function McpTab({ s }: { s: SettingsPanelState }) {
  const {
    confirmAction,
    settings,
    mcp,
    mcpLoading,
    expose,
    setExpose,
    httpEnabled,
    setHttpEnabled,
    deferTools,
    setDeferTools,
    alwaysLoad,
    setAlwaysLoad,
    workiqTenant,
    setWorkiqTenant,
    playwrightBrowser,
    setPlaywrightBrowser,
    mcpEditing,
    setMcpEditing,
    signInOwner,
    refreshMcp,
    toggleMcp,
    togglePreview,
    reloadMcpTools,
    reauthenticateWorkiq,
  } = s;
  return (
    <section>
      <McpExposeCard
        expose={expose}
        setExpose={setExpose}
        precursorEnabled={
          mcp.find((s) => s.name === "precursor")?.enabled ?? false
        }
        httpEnabled={httpEnabled}
        setHttpEnabled={setHttpEnabled}
        httpUrl={settings?.mcp_http_url ?? null}
        httpLoopbackOk={settings?.mcp_http_loopback_ok ?? true}
      />
      <Agent365Card
        tenant={workiqTenant}
        setTenant={setWorkiqTenant}
        discoveredTenant={
          settings?.workiq_tenant_discovered
            ? (settings?.workiq_tenant_id ?? "")
            : ""
        }
      />
      <PlaywrightCard
        browser={playwrightBrowser}
        setBrowser={setPlaywrightBrowser}
      />
      <label className="flex items-start gap-2 mb-3 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={deferTools}
          onChange={(e) => setDeferTools(e.target.checked)}
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
          onClick={() => setMcpEditing("new")}
          className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs whitespace-nowrap"
        >
          <Plus size={12} /> New
        </button>
      </div>
      {mcp.length === 0 ? (
        mcpLoading ? (
          <p className="flex items-center gap-2 text-xs text-muted">
            <RefreshCw size={12} className="animate-spin" />
            Loading MCP servers…
          </p>
        ) : (
          <p className="text-xs text-muted">No MCP servers registered.</p>
        )
      ) : (
        <ul className="space-y-2">
          {mcp.map((s) => (
            <McpServerCard
              key={s.name}
              server={s}
              onToggle={() => void toggleMcp(s.name, s.enabled)}
              alwaysLoad={
                deferTools ? (alwaysLoad[s.name] ?? false) : undefined
              }
              onToggleAlwaysLoad={() =>
                setAlwaysLoad((prev) => ({
                  ...prev,
                  [s.name]: !(prev[s.name] ?? false),
                }))
              }
              onReload={
                s.enabled
                  ? () => void reloadMcpTools(s.name)
                  : undefined
              }
              onTogglePreview={
                s.preview != null
                  ? () => void togglePreview(s.name, s.preview ?? false)
                  : undefined
              }
              onReauthenticate={
                s.oauth &&
                signInOwner.get(mcpAuthFamily(s.name)) === s.name
                  ? () => void reauthenticateWorkiq(s.name)
                  : undefined
              }
              sharedSignInWith={
                s.oauth &&
                signInOwner.get(mcpAuthFamily(s.name)) !== s.name
                  ? mcpServerLabel(
                      signInOwner.get(mcpAuthFamily(s.name)) ?? "",
                    )
                  : undefined
              }
              onEdit={() => setMcpEditing(s)}
              onDelete={async () => {
                if (s.id == null) return;
                if (
                  !(await confirmAction({
                    message: `Delete MCP server "${s.name}"?`,
                    confirmLabel: "Delete MCP server",
                    variant: "danger",
                  }))
                )
                  return;
                await api.mcp.remove(s.id);
                await refreshMcp();
              }}
            />
          ))}
        </ul>
      )}
      {mcpEditing && (
        <McpServerEditor
          server={mcpEditing === "new" ? null : mcpEditing}
          onClose={() => setMcpEditing(null)}
          onSaved={async () => {
            await refreshMcp();
            setMcpEditing(null);
          }}
        />
      )}
    </section>
  );
}
