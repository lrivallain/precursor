import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Download,
  ExternalLink,
  LayoutGrid,
  Plug,
  Puzzle,
  RefreshCw,
  Route as RouteIcon,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { getSection, getSettingsPage } from "../lib/plugins";
import { pluginStore } from "../lib/pluginStore";
import type { CatalogPlugin, InstalledPlugin, PluginEnvironment } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

/**
 * Settings → Plugins: what's installed, what each one brings, and a switch.
 *
 * Turning a plugin off is immediate and total — its sections vanish from the
 * SPA, its API routes answer 404 and its MCP servers leave the tool catalogue —
 * so this doubles as the "why is that section missing?" answer.
 */
export function PluginsSettings() {
  const confirmAction = useConfirm();
  const [plugins, setPlugins] = useState<InstalledPlugin[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogPlugin[]>([]);
  const [env, setEnv] = useState<PluginEnvironment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pkg, setPkg] = useState("");
  /** The package currently being installed, so only its button shows a spinner. */
  const [installing, setInstalling] = useState<string | null>(null);
  // Set once something has changed on disk: discovery only runs at startup, so
  // an install is inert until the process restarts.
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [restarting, setRestarting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [installed, entries] = await Promise.all([
        api.plugins.installed(),
        // The catalogue is bundled, so this can't fail for network reasons —
        // but an empty one is a perfectly valid state, not an error worth
        // taking the whole panel down for.
        api.plugins.catalog().catch(() => [] as CatalogPlugin[]),
      ]);
      setPlugins(installed);
      setCatalog(entries);
      setError(null);
    } catch (e) {
      setPlugins([]);
      setError(apiErrorMessage(e, "Failed to load plugins"));
    }
  }, []);

  useEffect(() => {
    void load();
    void api.plugins
      .environment()
      .then(setEnv)
      .catch(() => setEnv(null));
  }, [load]);

  /**
   * Turn the in-app installer on.
   *
   * Off by default because installing a package runs its code with Precursor's
   * privileges and the app has no authentication of its own — so this is an
   * explicit, deliberate act rather than something a stray request can do.
   */
  async function enableInstalling() {
    try {
      await api.settings.update({ plugin_install_enabled: true });
      setEnv(await api.plugins.environment());
    } catch (e) {
      setError(apiErrorMessage(e, "Could not enable in-app installing"));
    }
  }

  /**
   * Install one package and mark the instance as needing a restart.
   *
   * Shared by the free-form box and the catalogue, so both go through exactly
   * the same gated endpoint — the catalogue is a shortcut to a package name,
   * never a second, laxer way in.
   */
  async function installPackage(target: string, clearBox: boolean) {
    if (!target) return;
    setInstalling(target);
    setError(null);
    try {
      await api.plugins.install(target);
      if (clearBox) setPkg("");
      setRestartNeeded(true);
      await load();
    } catch (e) {
      setError(apiErrorMessage(e, "Install failed"));
    } finally {
      setInstalling(null);
    }
  }

  async function uninstall(plugin: InstalledPlugin) {
    const ok = await confirmAction({
      title: `Remove ${plugin.distribution ?? plugin.id}?`,
      message:
        "The package is uninstalled from Precursor's environment. Its data is untouched.",
      confirmLabel: "Uninstall",
      variant: "danger",
    });
    if (!ok) return;
    setBusy(plugin.id);
    setError(null);
    try {
      await api.plugins.uninstall(plugin.id);
      setRestartNeeded(true);
      await load();
    } catch (e) {
      setError(apiErrorMessage(e, "Uninstall failed"));
    } finally {
      setBusy(null);
    }
  }

  /** Restart, then wait for the server to answer again and reload the SPA. */
  async function restart() {
    setRestarting(true);
    setError(null);
    try {
      await api.plugins.restart();
    } catch (e) {
      setError(apiErrorMessage(e, "Restart failed"));
      setRestarting(false);
      return;
    }
    const deadline = Date.now() + 60_000;
    // Poll until the new process is serving, then reload so the SPA re-reads
    // the descriptors and imports any newly installed plugin bundle.
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 1000));
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        if (res.ok) {
          window.location.reload();
          return;
        }
      } catch {
        /* still down — keep waiting */
      }
    }
    setError("Precursor did not come back within a minute. Restart it yourself.");
    setRestarting(false);
  }

  async function toggle(plugin: InstalledPlugin, enabled: boolean) {
    setBusy(plugin.id);
    try {
      await api.plugins.setEnabled(plugin.id, enabled);
      // Republish the descriptors so the sidebar, home launcher, palette and
      // router pick the change up immediately — no reload, and the panel the
      // user is standing in stays open.
      await pluginStore.refresh();
      await load();
    } catch (e) {
      setError(apiErrorMessage(e, "Failed to update the plugin"));
    } finally {
      setBusy(null);
    }
  }

  /** Catalogue entries this instance doesn't already have — what's left to add. */
  const available = useMemo(() => catalog.filter((e) => !e.installed), [catalog]);

  /**
   * Documentation page per catalogue entry, keyed by plugin id, so an *installed*
   * plugin can still link to the write-up that convinced you to install it.
   */
  const docsById = useMemo(
    () => new Map(catalog.map((e) => [e.id, e.docs_path])),
    [catalog],
  );

  if (plugins === null) {
    return <div className="text-sm text-muted">Loading plugins…</div>;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-semibold">Plugins</h3>
        <p className="text-xs text-muted">
          Python packages that extend Precursor — with their own sections, API
          routes and MCP tools.
        </p>
      </div>

      {restartNeeded && (
        <div className="flex items-center gap-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2">
          <RefreshCw
            size={14}
            className={`shrink-0 text-amber-600 dark:text-amber-400 ${restarting ? "animate-spin" : ""}`}
          />
          <span className="min-w-0 flex-1 text-xs">
            Precursor must restart to pick this up — plugins are discovered once,
            at startup.
          </span>
          <button
            type="button"
            disabled={restarting}
            onClick={() => void restart()}
            className="shrink-0 rounded border border-amber-500/40 bg-amber-500/15 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-500/25 disabled:opacity-60 dark:text-amber-300"
          >
            {restarting ? "Restarting…" : "Restart now"}
          </button>
        </div>
      )}

      {error && (
        <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </div>
      )}

      {available.length > 0 && (
        <Catalog
          entries={available}
          canInstall={env?.can_install === true}
          installing={installing}
          onInstall={(distribution) => void installPackage(distribution, false)}
          onPrepare={setPkg}
        />
      )}

      {/* Below the catalogue on purpose: "Show command" loads a package name
          into this box, so it has to be the next thing the eye lands on. */}
      <InstallBox
        env={env}
        pkg={pkg}
        onPkgChange={setPkg}
        installing={installing !== null}
        onInstall={() => void installPackage(pkg.trim(), true)}
        onEnableInstalling={() => void enableInstalling()}
      />

      {plugins.length === 0 ? (
        <div className="rounded border border-border bg-surface/60 px-3 py-6 text-center text-sm text-muted">
          No plugins installed.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
            Installed
          </h4>
          <ul className="flex flex-col gap-3">
          {plugins.map((plugin) => (
            <li
              key={plugin.id}
              className="rounded-lg border border-border bg-surface/60 p-4 flex flex-col gap-3"
            >
              <div className="flex items-start gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                  <Puzzle size={16} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">
                      {plugin.distribution ?? plugin.id}
                    </span>
                    {plugin.version && (
                      <span className="shrink-0 rounded bg-surface px-1.5 py-0.5 text-[11px] text-muted">
                        v{plugin.version}
                      </span>
                    )}
                    {plugin.homepage && (
                      <a
                        href={plugin.homepage}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="shrink-0 text-muted hover:text-accent"
                        aria-label="Open the plugin's homepage"
                        data-tooltip="Homepage"
                      >
                        <ExternalLink size={13} />
                      </a>
                    )}
                    {docsById.has(plugin.id) && (
                      <a
                        href={docsById.get(plugin.id)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="shrink-0 text-muted hover:text-accent"
                        aria-label="Open the plugin's documentation"
                        data-tooltip="Documentation"
                      >
                        <BookOpen size={13} />
                      </a>
                    )}
                  </div>
                  {plugin.summary && (
                    <p className="mt-0.5 text-xs text-muted">{plugin.summary}</p>
                  )}
                </div>
                {env?.can_install && (
                  <button
                    type="button"
                    disabled={busy === plugin.id}
                    onClick={() => void uninstall(plugin)}
                    className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-red-500 disabled:opacity-50"
                    aria-label="Uninstall plugin"
                    data-tooltip="Uninstall"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
                <label className="flex shrink-0 cursor-pointer items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={plugin.enabled}
                    disabled={busy === plugin.id || plugin.error !== null}
                    onChange={(e) => void toggle(plugin, e.target.checked)}
                    className="accent-accent"
                    aria-label={plugin.enabled ? "Disable plugin" : "Enable plugin"}
                  />
                  <span className="text-muted">{plugin.enabled ? "Enabled" : "Disabled"}</span>
                </label>
              </div>

              {plugin.error ? (
                <div className="flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 px-2.5 py-2 text-xs text-red-500">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <span className="min-w-0 break-words">
                    Failed to load: {plugin.error}
                  </span>
                </div>
              ) : (
                <>
                  <Contributions plugin={plugin} />
                  {plugin.enabled && <MissingFrontend plugin={plugin} />}
                </>
              )}
            </li>
          ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * The bundled catalogue: plugins you could add, with a one-click install.
 *
 * It is a *shortcut to a package name*, not a second install path — the button
 * calls the same gated endpoint the free-form box does. When the app isn't
 * allowed to install (not opted in, not on loopback), the entry still earns its
 * place: it loads the name into the box above so the copyable command is right.
 */
function Catalog({
  entries,
  canInstall,
  installing,
  onInstall,
  onPrepare,
}: {
  entries: CatalogPlugin[];
  canInstall: boolean;
  installing: string | null;
  onInstall: (distribution: string) => void;
  onPrepare: (distribution: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-0.5">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
          Available
        </h4>
        <p className="text-[11px] text-muted">
          Plugins we know about, shipped with Precursor — no network involved.
        </p>
      </div>
      <ul className="flex flex-col gap-3">
        {entries.map((entry) => (
          <li
            key={entry.id}
            className="flex flex-col gap-3 rounded-lg border border-border bg-surface/60 p-4"
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                <Puzzle size={16} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-medium">{entry.title}</span>
                  {entry.recommended && (
                    <span
                      className="inline-flex shrink-0 items-center gap-1 rounded bg-accent/10 px-1.5 py-0.5 text-[11px] font-medium text-accent"
                      data-tooltip="Maintained or vetted by the Precursor project"
                    >
                      <Sparkles size={10} />
                      Recommended
                    </span>
                  )}
                  <a
                    href={entry.docs_path}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 text-muted hover:text-accent"
                    aria-label={`Read the ${entry.title} documentation`}
                    data-tooltip="Documentation"
                  >
                    <BookOpen size={13} />
                  </a>
                  {entry.homepage && (
                    <a
                      href={entry.homepage}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="shrink-0 text-muted hover:text-accent"
                      aria-label={`Open the ${entry.title} homepage`}
                      data-tooltip="Homepage"
                    >
                      <ExternalLink size={13} />
                    </a>
                  )}
                </div>
                <p className="mt-0.5 text-xs text-muted">{entry.summary}</p>
                <p className="mt-1 font-mono text-[11px] text-muted">
                  {entry.distribution}
                  {entry.tags.length > 0 && (
                    <span className="font-sans"> · {entry.tags.join(" · ")}</span>
                  )}
                </p>
              </div>
              <button
                type="button"
                disabled={installing !== null}
                onClick={() =>
                  canInstall ? onInstall(entry.distribution) : onPrepare(entry.distribution)
                }
                className="shrink-0 rounded border border-accent/30 bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
              >
                {installing === entry.distribution
                  ? "Installing…"
                  : canInstall
                    ? "Install"
                    : "Show command"}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The three things a plugin can bring, listed only when it brings them. */
function Contributions({ plugin }: { plugin: InstalledPlugin }) {
  const rows: Array<{ icon: typeof Plug; label: string; items: string[] }> = [
    {
      icon: LayoutGrid,
      label: "Sections",
      items: plugin.sections.map((s) => s.title),
    },
    { icon: RouteIcon, label: "API", items: plugin.routes },
    {
      icon: Plug,
      label: "MCP servers",
      items: plugin.mcp_servers.map((s) => s.name),
    },
  ].filter((r) => r.items.length > 0);

  if (rows.length === 0) {
    return <p className="text-xs text-muted">Contributes nothing yet.</p>;
  }

  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((row) => (
        <div key={row.label} className="flex items-center gap-2 text-xs">
          <row.icon size={12} className="shrink-0 text-muted" />
          <span className="shrink-0 text-muted">{row.label}</span>
          <span className="min-w-0 truncate font-mono text-[11px]">
            {row.items.join(", ")}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * Install a plugin — by running it here when that's safe, or by showing the
 * exact command otherwise.
 *
 * The command is environment-specific: a `uv tool install` of Precursor lives in
 * an isolated environment that `pip install` silently fails to extend, so the
 * backend reports which installer actually owns this instance.
 */
function InstallBox({
  env,
  pkg,
  onPkgChange,
  installing,
  onInstall,
  onEnableInstalling,
}: {
  env: PluginEnvironment | null;
  pkg: string;
  onPkgChange: (v: string) => void;
  installing: boolean;
  onInstall: () => void;
  onEnableInstalling: () => void;
}) {
  if (env === null) return null;

  const command = env.command_template.replace("<package>", pkg.trim() || "<package>");

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface/60 p-3">
      <div className="flex items-center gap-2">
        <Download size={13} className="shrink-0 text-muted" />
        <span className="text-xs font-medium">Install a plugin</span>
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={pkg}
          onChange={(e) => onPkgChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && env.can_install) onInstall();
          }}
          placeholder="package name, e.g. precursor-kanban"
          className="min-w-0 flex-1 rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
        {env.can_install && (
          <button
            type="button"
            disabled={installing || pkg.trim().length === 0}
            onClick={onInstall}
            className="shrink-0 rounded border border-accent/30 bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            {installing ? "Installing…" : "Install"}
          </button>
        )}
      </div>
      <p className="text-[11px] text-muted">
        {env.can_install
          ? "Or run it yourself, in Precursor's own environment:"
          : env.reason
            ? `${env.reason} Run it yourself, in Precursor's own environment:`
            : "Run it yourself, in Precursor's own environment:"}{" "}
        <code className="rounded bg-surface px-1 py-0.5">{command}</code>
      </p>
      {!env.can_install && env.installable_here && (
        <label className="flex cursor-pointer items-start gap-2 text-[11px] text-muted">
          <input
            type="checkbox"
            checked={false}
            onChange={() => onEnableInstalling()}
            className="mt-0.5 accent-accent"
          />
          <span>
            Let Precursor install packages for me. Installing runs the package's
            own code with Precursor's privileges, so this stays off unless you
            ask for it.
          </span>
        </label>
      )}
    </div>
  );
}

/**
 * A plugin that publishes UI but whose bundle never arrived.
 *
 * A plugin's frontend is a build product shipped inside its wheel. If it is
 * missing — a package built without it — the backend still advertises the
 * section, the SPA has nothing to import, `registerSection` never runs, and the
 * section is dropped. Everything else looks healthy: installed, enabled, no
 * error. Without this notice the only symptom is a section that silently isn't
 * there.
 */
function MissingFrontend({ plugin }: { plugin: InstalledPlugin }) {
  // A plugin bundled into core's own build also has no `entry`, but it *is*
  // registered — so "advertises UI, has no entry, and nothing registered under
  // its id" is what identifies a genuinely missing bundle.
  const advertised = plugin.extensions.filter(
    (e) => e.kind === "section" || e.kind === "settings-page",
  );
  if (advertised.length === 0 || plugin.entry !== null) return null;
  const registered = advertised.some(
    (e) =>
      (e.kind === "section" && getSection(e.id) != null) ||
      (e.kind === "settings-page" && getSettingsPage(e.id) != null),
  );
  if (registered) return null;

  return (
    <div className="flex items-start gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-xs">
      <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
      <span className="min-w-0">
        Its interface is missing, so nothing it contributes to the UI will appear.
        A plugin's frontend is built into its package — reinstall or upgrade the
        package to get one that carries it.
      </span>
    </div>
  );
}
