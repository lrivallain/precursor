import { useState } from "react";
import { X } from "lucide-react";
import { api } from "../../lib/api";
import type { MCPServerStatus, MCPServerCreate, MCPServerUpdate } from "../../lib/types";

export function McpServerEditor({
  server,
  onClose,
  onSaved,
}: {
  server: MCPServerStatus | null;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const isNew = server === null;
  const [name, setName] = useState(server?.name ?? "");
  const [transport, setTransport] = useState<"streamable_http" | "stdio">(
    (server?.transport as "streamable_http" | "stdio") ?? "streamable_http",
  );
  const [url, setUrl] = useState(server?.url ?? "");
  const [command, setCommand] = useState(server?.command_bin ?? "");
  const [argsList, setArgsList] = useState<string[]>(server?.args ?? []);
  // Existing header values are not exposed by the API for security; keys are
  // shown so the user knows what is set, but values must be re-entered.
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>(() =>
    server?.header_keys?.map((k) => ({ key: k, value: "" })) ?? [],
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSave(): Promise<void> {
    setError(null);
    setSaving(true);
    try {
      const headerMap: Record<string, string> = {};
      for (const h of headers) {
        if (h.key.trim()) headerMap[h.key.trim()] = h.value;
      }
      const cleanArgs = argsList.map((a) => a).filter((a) => a !== undefined);

      if (isNew) {
        const payload: MCPServerCreate = {
          name: name.trim(),
          transport,
          ...(transport === "streamable_http"
            ? { url: url.trim(), headers: headerMap }
            : { command: command.trim(), args: cleanArgs }),
        };
        await api.mcp.create(payload);
      } else if (server?.id != null) {
        const payload: MCPServerUpdate = {
          name: name.trim(),
          transport,
          ...(transport === "streamable_http"
            ? { url: url.trim(), headers: headerMap }
            : { command: command.trim(), args: cleanArgs }),
        };
        await api.mcp.update(server.id, payload);
      }
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bg border border-border rounded-lg w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between p-3 border-b border-border">
          <h3 className="text-sm font-medium">
            {isNew ? "New MCP server" : `Edit ${server?.name}`}
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface text-muted"
          >
            <X size={16} />
          </button>
        </header>
        <div className="p-3 space-y-3 overflow-y-auto">
          <div>
            <label className="block text-xs text-muted mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-server"
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
            <p className="text-[10px] text-muted mt-1">
              Lowercase letters, digits, hyphens. Max 64 chars.
            </p>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Transport</label>
            <div className="flex gap-3 text-xs">
              <label className="flex items-center gap-1 cursor-pointer">
                <input
                  type="radio"
                  name="transport"
                  checked={transport === "streamable_http"}
                  onChange={() => setTransport("streamable_http")}
                  className="accent-accent"
                />
                HTTP (streamable)
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input
                  type="radio"
                  name="transport"
                  checked={transport === "stdio"}
                  onChange={() => setTransport("stdio")}
                  className="accent-accent"
                />
                stdio (subprocess)
              </label>
            </div>
          </div>

          {transport === "streamable_http" ? (
            <>
              <div>
                <label className="block text-xs text-muted mb-1">URL</label>
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/mcp"
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent font-mono"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs text-muted">Headers</label>
                  <button
                    type="button"
                    onClick={() =>
                      setHeaders((prev) => [...prev, { key: "", value: "" }])
                    }
                    className="text-[10px] text-accent hover:underline"
                  >
                    + Add header
                  </button>
                </div>
                {!isNew && server?.header_keys?.length ? (
                  <p className="text-[10px] text-muted mb-1">
                    Existing header values are hidden — re-enter them to keep
                    them, or remove the row to drop them.
                  </p>
                ) : null}
                <div className="space-y-1">
                  {headers.map((h, i) => (
                    <div key={i} className="flex gap-1">
                      <input
                        type="text"
                        value={h.key}
                        onChange={(e) =>
                          setHeaders((prev) =>
                            prev.map((x, j) =>
                              j === i ? { ...x, key: e.target.value } : x,
                            ),
                          )
                        }
                        placeholder="Header-Name"
                        className="flex-1 bg-surface border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent"
                      />
                      <input
                        type="text"
                        value={h.value}
                        onChange={(e) =>
                          setHeaders((prev) =>
                            prev.map((x, j) =>
                              j === i ? { ...x, value: e.target.value } : x,
                            ),
                          )
                        }
                        placeholder="value"
                        className="flex-1 bg-surface border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          setHeaders((prev) => prev.filter((_, j) => j !== i))
                        }
                        className="p-1 rounded hover:bg-surface text-muted hover:text-red-500"
                        data-tooltip="Remove"
                        aria-label="Remove header"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="block text-xs text-muted mb-1">Command</label>
                <input
                  type="text"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx"
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent font-mono"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs text-muted">Arguments</label>
                  <button
                    type="button"
                    onClick={() => setArgsList((prev) => [...prev, ""])}
                    className="text-[10px] text-accent hover:underline"
                  >
                    + Add arg
                  </button>
                </div>
                <div className="space-y-1">
                  {argsList.map((a, i) => (
                    <div key={i} className="flex gap-1">
                      <input
                        type="text"
                        value={a}
                        onChange={(e) =>
                          setArgsList((prev) =>
                            prev.map((x, j) => (j === i ? e.target.value : x)),
                          )
                        }
                        className="flex-1 bg-surface border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          setArgsList((prev) => prev.filter((_, j) => j !== i))
                        }
                        className="p-1 rounded hover:bg-surface text-muted hover:text-red-500"
                        aria-label="Remove"
                        data-tooltip="Remove"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {error && (
            <div className="text-xs text-red-500 whitespace-pre-wrap break-words">
              {error}
            </div>
          )}
        </div>
        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving..." : isNew ? "Create" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}
