import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "../lib/api";
import { setTheme, getStoredTheme } from "../lib/theme";
import type { MCPServerStatus, Settings, Theme } from "../lib/types";

interface Props {
  onClose: () => void;
}

export function SettingsPanel({ onClose }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [mcp, setMcp] = useState<MCPServerStatus[]>([]);
  const [theme, setThemeState] = useState<Theme>(getStoredTheme());
  const [model, setModel] = useState("");
  const [repo, setRepo] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void (async () => {
      const s = await api.getSettings();
      setSettings(s);
      setModel(s.llm_model);
      setRepo(s.github_repo);
      try {
        setMcp(await api.listMcpServers());
      } catch {
        setMcp([]);
      }
    })();
  }, []);

  async function save(): Promise<void> {
    setSaving(true);
    try {
      const payload: Parameters<typeof api.updateSettings>[0] = {
        theme,
        llm_model: model,
        github_repo: repo,
      };
      if (githubToken) {
        payload.api_keys = { github_token: githubToken };
      }
      const updated = await api.updateSettings(payload);
      setSettings(updated);
      setGithubToken("");
      setTheme(theme);
    } finally {
      setSaving(false);
    }
  }

  async function toggleMcp(name: string, connected: boolean): Promise<void> {
    const next = connected
      ? await api.disconnectMcpServer(name)
      : await api.connectMcpServer(name);
    setMcp((prev) => prev.map((s) => (s.name === name ? next : s)));
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50">
      <div className="w-[min(420px,100%)] h-full bg-bg border-l border-border flex flex-col">
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold">Settings</h2>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-surface" aria-label="Close">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          <section>
            <h3 className="text-sm font-medium mb-2">Appearance</h3>
            <div className="flex gap-2">
              {(["light", "dark", "system"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setThemeState(t)}
                  className={`px-3 py-1.5 rounded border text-sm capitalize ${
                    theme === t ? "border-accent text-accent" : "border-border text-text"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </section>

          <section>
            <h3 className="text-sm font-medium mb-2">Model</h3>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="openai/gpt-4o-mini"
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
            <p className="text-xs text-muted mt-1">
              Any model id supported by the GitHub Models catalog.
            </p>
          </section>

          <section>
            <h3 className="text-sm font-medium mb-2">GitHub</h3>
            <label className="block text-xs text-muted mb-1">Reference repository</label>
            <input
              type="text"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="owner/name"
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent mb-3"
            />
            <label className="block text-xs text-muted mb-1">
              Personal access token{" "}
              {settings?.api_keys_present?.github_token && (
                <span className="text-green-500">(configured)</span>
              )}
            </label>
            <input
              type="password"
              value={githubToken}
              onChange={(e) => setGithubToken(e.target.value)}
              placeholder="ghp_..."
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
          </section>

          <section>
            <h3 className="text-sm font-medium mb-2">MCP servers</h3>
            {mcp.length === 0 ? (
              <p className="text-xs text-muted">
                No MCP servers configured yet. Define them in your environment or via the plugin
                API.
              </p>
            ) : (
              <ul className="space-y-2">
                {mcp.map((s) => {
                  const connected = s.state === "connected";
                  return (
                    <li
                      key={s.name}
                      className="flex items-center justify-between border border-border rounded px-2 py-1.5"
                    >
                      <div>
                        <div className="text-sm">{s.name}</div>
                        <div className="text-[11px] text-muted">{s.transport} — {s.state}</div>
                      </div>
                      <button
                        onClick={() => void toggleMcp(s.name, connected)}
                        className={`text-xs px-2 py-1 rounded border ${
                          connected
                            ? "border-accent text-accent"
                            : "border-border text-muted hover:text-text"
                        }`}
                      >
                        {connected ? "Disconnect" : "Connect"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>

        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={saving}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}
