import { useState } from "react";
import { ChevronDown, ChevronRight, Pencil, Trash2, LogIn, RefreshCw } from "lucide-react";
import type { MCPServerStatus } from "../../lib/types";
import { serverStateColor } from "./mcpServerList";

interface McpServerCardProps {
  server: MCPServerStatus;
  onToggle: () => void;
  /** Pin state for on-demand tool loading; undefined hides the toggle. */
  alwaysLoad?: boolean;
  onToggleAlwaysLoad?: () => void;
  onReload?: () => void;
  onTogglePreview?: () => void;
  onReauthenticate?: () => void;
  /**
   * Label of the server this one signs in with, when it shares a credential and
   * so carries no button of its own (see {@link mcpAuthFamily}).
   */
  sharedSignInWith?: string;
  onEdit?: () => void;
  onDelete?: () => void;
}

export function McpServerCard(props: McpServerCardProps) {
  const { server } = props;
  const [open, setOpen] = useState(false);
  return (
    <li className="border border-border rounded">
      <div className="flex items-center justify-between px-2 py-1.5 gap-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 text-left flex-1 min-w-0"
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <div className="min-w-0">
            <div className="text-sm flex items-center gap-1.5">
              <span>{server.name}</span>
              {server.builtin && (
                <span className="text-[10px] text-muted border border-border rounded px-1 py-px">
                  built-in
                </span>
              )}
            </div>
            <div className={`text-[11px] ${serverStateColor(server.state)}`}>
              {server.transport} — {server.state}
              {server.tools.length > 0 && ` · ${server.tools.length} tools`}
            </div>
          </div>
        </button>
        <CardSwitches {...props} />
        <SignInControl {...props} />
        <CardButtons {...props} />
      </div>
      {open && <ServerDetails server={server} />}
    </li>
  );
}

function CardSwitches({ server, onToggle, alwaysLoad, onToggleAlwaysLoad, onTogglePreview }: McpServerCardProps) {
  return (
    <>
      <label className="flex items-center gap-1 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={server.enabled}
          onChange={onToggle}
          className="accent-accent"
        />
        <span className="text-muted">enabled</span>
      </label>
      {alwaysLoad !== undefined && onToggleAlwaysLoad && server.enabled && (
        <label
          className="flex items-center gap-1 text-xs cursor-pointer"
          data-tooltip="Send all this server's tool definitions with every request instead of loading them on demand"
        >
          <input
            type="checkbox"
            checked={alwaysLoad}
            onChange={onToggleAlwaysLoad}
            className="accent-accent"
          />
          <span className="text-muted">always load</span>
        </label>
      )}
      {onTogglePreview && (
        <label
          className="flex items-center gap-1 text-xs cursor-pointer"
          data-tooltip="Use the hosted WorkIQ endpoint (OAuth sign-in) to enable writes"
        >
          <input
            type="checkbox"
            checked={server.preview ?? false}
            onChange={onTogglePreview}
            className="accent-accent"
          />
          <span className="text-muted">preview</span>
        </label>
      )}
    </>
  );
}

function SignInControl({ server, onReauthenticate, sharedSignInWith }: McpServerCardProps) {
  if (!server.oauth) return null;
  if (onReauthenticate) {
    const needsAuth = server.state === "needs_auth";
    return (
      <button
        type="button"
        onClick={onReauthenticate}
        disabled={server.state === "connecting"}
        data-tooltip="Open the WorkIQ browser sign-in to refresh expired credentials"
        className={`flex items-center gap-1 px-2 py-1 rounded text-xs whitespace-nowrap disabled:opacity-50 ${
          needsAuth ? "bg-accent text-white" : "border border-border text-muted hover:text-text"
        }`}
      >
        <LogIn size={12} />
        {needsAuth ? "Sign in" : "Re-authenticate"}
      </button>
    );
  }
  if (!sharedSignInWith) return null;
  return (
    <span
      className="text-[11px] text-muted whitespace-nowrap"
      data-tooltip={`One Entra credential covers both, so signing in to ${sharedSignInWith} authenticates this server too.`}
    >
      signs in with {sharedSignInWith}
    </span>
  );
}

function CardButtons({ server, onReload, onEdit, onDelete }: McpServerCardProps) {
  const editable = !server.builtin;
  return (
    <>
      {onReload && (
        <button
          type="button"
          onClick={onReload}
          disabled={server.state === "connecting"}
          data-tooltip="Reload this server's tool catalogue"
          aria-label="Reload tools"
          className="p-1 rounded hover:bg-bg text-muted hover:text-text disabled:opacity-50"
        >
          <RefreshCw size={14} />
        </button>
      )}
      {editable && onEdit && (
        <button
          type="button"
          onClick={onEdit}
          data-tooltip="Edit"
          aria-label="Edit server"
          className="p-1 rounded hover:bg-bg text-muted hover:text-text"
        >
          <Pencil size={14} />
        </button>
      )}
      {editable && onDelete && (
        <button
          type="button"
          onClick={onDelete}
          data-tooltip="Delete"
          aria-label="Delete server"
          className="p-1 rounded hover:bg-bg text-muted hover:text-red-500"
        >
          <Trash2 size={14} />
        </button>
      )}
    </>
  );
}

function emptyToolsHint(server: MCPServerStatus): string {
  if (server.state === "needs_auth") return "WorkIQ sign-in expired — select Sign in to re-authenticate.";
  return server.enabled
    ? "Use the reload button to refresh the tool catalogue."
    : "Enable the server to discover its tools.";
}

function ServerDetails({ server }: { server: MCPServerStatus }) {
  return (
    <div className="border-t border-border px-3 py-2 text-xs space-y-2">
      {server.url && (
        <div>
          <span className="text-muted">URL:</span>{" "}
          <span className="font-mono break-all">{server.url}</span>
        </div>
      )}
      {server.auth === "azure_cli" && (
        <div>
          <span className="text-muted">Sign-in:</span> Entra ID via Azure CLI
          {server.auth_tenant ? ` (tenant ${server.auth_tenant})` : ""}
        </div>
      )}
      {server.command && (
        <div>
          <span className="text-muted">Command:</span>{" "}
          <span className="font-mono break-all">{server.command}</span>
        </div>
      )}
      {server.error && (
        <div
          className={`whitespace-pre-wrap break-words ${
            server.state === "needs_auth" ? "text-amber-500" : "text-red-500"
          }`}
        >
          {server.error}
        </div>
      )}
      {server.tools.length === 0 ? (
        <div className="text-muted italic">{emptyToolsHint(server)}</div>
      ) : (
        <div>
          <div className="text-muted mb-1">Available tools</div>
          <ul className="space-y-1 max-h-60 overflow-y-auto pr-1">
            {server.tools.map((t) => (
              <li key={t.name}>
                <div className="font-mono text-[11px]">{t.name}</div>
                {t.description && (
                  <div className="text-[11px] text-muted">{t.description}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
