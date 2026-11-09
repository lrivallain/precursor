import { useState } from "react";
import type { SettingsSection } from "./settingsSection";
import { useMcpServers } from "./useMcpServers";

/**
 * MCP tab: what Precursor serves over its own MCP server, the built-in servers'
 * options and on-demand tool loading. The server list itself saves as you go.
 */
export function useMcpSettings() {
  const servers = useMcpServers();
  // Per-section exposure of Precursor's own capabilities over the built-in
  // "precursor" MCP server, and its HTTP (localhost) transport.
  const [expose, setExpose] = useState<Record<string, boolean>>({});
  const [httpEnabled, setHttpEnabled] = useState(false);
  const [deferTools, setDeferTools] = useState(true);
  const [alwaysLoad, setAlwaysLoad] = useState<Record<string, boolean>>({});
  const [workiqTenant, setWorkiqTenant] = useState("");
  // Browser channel the built-in Playwright MCP server drives.
  const [playwrightBrowser, setPlaywrightBrowser] = useState("msedge");

  const toggleAlwaysLoad = (name: string): void =>
    setAlwaysLoad((prev) => ({ ...prev, [name]: !(prev[name] ?? false) }));

  const section: SettingsSection = {
    hydrate: (s) => {
      setExpose(s.mcp_expose ?? {});
      setHttpEnabled(s.mcp_http_enabled);
      setDeferTools(s.mcp_defer_tools ?? true);
      setAlwaysLoad(s.mcp_always_load ?? {});
      // A discovered tenant shows as a placeholder rather than typed input, so
      // saving doesn't silently pin it into the database.
      setWorkiqTenant(s.workiq_tenant_discovered ? "" : s.workiq_tenant_id);
      setPlaywrightBrowser(s.playwright_browser);
    },
    load: () => servers.refresh(),
    payload: () => ({
      mcp_expose: expose,
      mcp_http_enabled: httpEnabled,
      mcp_defer_tools: deferTools,
      mcp_always_load: alwaysLoad,
      workiq_tenant_id: workiqTenant.trim(),
      playwright_browser: playwrightBrowser,
    }),
    snapshot: [expose, httpEnabled, deferTools, alwaysLoad, workiqTenant, playwrightBrowser],
  };

  return {
    servers,
    expose, setExpose,
    httpEnabled, setHttpEnabled,
    deferTools, setDeferTools,
    alwaysLoad, toggleAlwaysLoad,
    workiqTenant, setWorkiqTenant,
    playwrightBrowser, setPlaywrightBrowser,
    section,
  };
}

export type McpSettings = ReturnType<typeof useMcpSettings>;
