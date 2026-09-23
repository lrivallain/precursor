import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  BookOpen,
  Check,
  ChevronDown,
  CircleArrowUp,
  Copy,
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
import type {
  CatalogPlugin,
  InstalledPlugin,
  PluginEnvironment,
  PluginInstallResult,
  PluginUpdate,
} from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import {
  LatestRelease,
  SourceBadge,
  installCommand,
  VersionSelect,
  isLookupSpec,
  requirementFor,
  sourceSpec,
  usePluginVersions,
} from "./PluginVersions";

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
  /** The nightly the host itself moved to while installing, if it had to. */
  const [hostUpgrade, setHostUpgrade] = useState<string | null>(null);
  /** Newest release per installed plugin, keyed by plugin id. Loaded lazily. */
  const [updates, setUpdates] = useState<Map<string, PluginUpdate>>(new Map());
  const [checkingUpdates, setCheckingUpdates] = useState(false);
  /**
   * Plugins moved to another release this session, keyed by id. They keep
   * running the old code until the restart, so the card says so instead of
   * offering the same upgrade again.
   */
  const [upgraded, setUpgraded] = useState<Record<string, string>>({});

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

  /**
   * Ask each installed plugin's source for its newest release.
   *
   * Separate from `load` and never awaited by it: it goes out to PyPI or
   * GitHub, and the panel must not wait on the network to list what is
   * installed. A failure here only means no "update available" hints.
   */
  const loadUpdates = useCallback(async (refresh = false) => {
    setCheckingUpdates(true);
    try {
      const rows = await api.plugins.updates(refresh);
      setUpdates(new Map(rows.map((row) => [row.id, row])));
    } catch {
      /* offline or rate limited — the list stays usable without hints */
    } finally {
      setCheckingUpdates(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadUpdates();
    void api.plugins
      .environment()
      .then(setEnv)
      .catch(() => setEnv(null));
  }, [load, loadUpdates]);

  /**
   * Turn the in-app installer on or off.
   *
   * Off by default because installing a package runs its code with Precursor's
   * privileges and the app has no authentication of its own — so this is an
   * explicit, deliberate act rather than something a stray request can do. It
   * is also revocable: the consent lives at the top of the panel and stays
   * visible once granted, because permission you can grant but not withdraw is
   * not really permission.
   */
  async function setInstallPermission(enabled: boolean) {
    try {
      await api.settings.update({ plugin_install_enabled: enabled });
      setEnv(await api.plugins.environment());
    } catch (e) {
      setError(apiErrorMessage(e, "Could not change the install permission"));
    }
  }

  /** Bookkeeping shared by every action that changes what is on disk. */
  function changedOnDisk(result: PluginInstallResult) {
    if (result.host_upgrade) setHostUpgrade(result.host_upgrade);
    setRestartNeeded(true);
  }

  /**
   * Install one package and mark the instance as needing a restart.
   *
   * Shared by the free-form box and the catalogue, so both go through exactly
   * the same gated endpoint — the catalogue is a shortcut to a package name,
   * never a second, laxer way in. `spec` is a package name, a GitHub link or a
   * requirement; `key` identifies the button that shows the spinner.
   */
  async function installPackage(
    spec: string,
    version: string | null,
    key: string,
    clearBox: boolean,
  ) {
    if (!spec) return;
    setInstalling(key);
    setError(null);
    try {
      changedOnDisk(await api.plugins.install(spec, version));
      if (clearBox) setPkg("");
      await load();
    } catch (e) {
      setError(apiErrorMessage(e, "Install failed"));
    } finally {
      setInstalling(null);
    }
  }

  /** Move one plugin to `version`, or to its newest release. */
  async function upgradePlugin(plugin: InstalledPlugin, version: string | null) {
    setBusy(plugin.id);
    setError(null);
    try {
      const result = await api.plugins.upgrade(plugin.id, version);
      changedOnDisk(result);
      setUpgraded((prev) => ({
        ...prev,
        [plugin.id]: result.version ?? version ?? "the newest release",
      }));
    } catch (e) {
      setError(apiErrorMessage(e, "Upgrade failed"));
    } finally {
      setBusy(null);
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
      const result = await api.plugins.uninstall(plugin.id);
      changedOnDisk(result);
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

      <InstallPermission
        env={env}
        onChange={(enabled) => void setInstallPermission(enabled)}
      />

      {restartNeeded && (
        <div className="flex items-center gap-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2">
          <RefreshCw
            size={14}
            className={`shrink-0 text-amber-600 dark:text-amber-400 ${restarting ? "animate-spin" : ""}`}
          />
          <span className="min-w-0 flex-1 text-xs">
            {env?.restart_supported === false
              ? "Restart Precursor the way you started it to pick this up — plugins are discovered once, at startup."
              : "Precursor must restart to pick this up — plugins are discovered once, at startup."}
            {hostUpgrade && (
              <>
                {" "}
                Precursor itself was updated to <code>{hostUpgrade}</code> too: the
                nightly build it was installed from is no longer published.
              </>
            )}
          </span>
          {env?.restart_supported !== false && (
            <button
              type="button"
              disabled={restarting}
              onClick={() => void restart()}
              className="shrink-0 rounded border border-amber-500/40 bg-amber-500/15 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-500/25 disabled:opacity-60 dark:text-amber-300"
            >
              {restarting ? "Restarting…" : "Restart now"}
            </button>
          )}
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
          commandTemplate={env?.command_template ?? null}
          installing={installing}
          onInstall={(entry, spec, version) =>
            void installPackage(spec, version, entry.distribution, false)
          }
        />
      )}

      {/* Below the catalogue: this is the escape hatch for a package that
          isn't listed, so it shouldn't outrank the curated entries. */}
      <InstallBox
        env={env}
        pkg={pkg}
        onPkgChange={setPkg}
        installing={installing !== null}
        onInstall={(version) => void installPackage(pkg.trim(), version, pkg.trim(), true)}
      />

      {plugins.length === 0 ? (
        <div className="rounded border border-border bg-surface/60 px-3 py-6 text-center text-sm text-muted">
          No plugins installed.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h4 className="flex-1 text-xs font-semibold uppercase tracking-wide text-muted">
              Installed
            </h4>
            <button
              type="button"
              disabled={checkingUpdates}
              onClick={() => void loadUpdates(true)}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted hover:bg-surface hover:text-accent disabled:opacity-60"
              data-tooltip="Ask each plugin's source — PyPI or GitHub — for its newest release"
            >
              <RefreshCw size={11} className={checkingUpdates ? "animate-spin" : ""} />
              {checkingUpdates ? "Checking…" : "Check for updates"}
            </button>
          </div>
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

              <PluginVersionRow
                plugin={plugin}
                update={updates.get(plugin.id) ?? null}
                canInstall={env?.can_install === true}
                commandTemplate={env?.command_template ?? null}
                busy={busy === plugin.id}
                upgradedTo={upgraded[plugin.id] ?? null}
                onUpgrade={(version) => void upgradePlugin(plugin, version)}
              />

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
/**
 * The bundled catalogue: plugins you could add, with a one-click install.
 *
 * Installing from here is a *shortcut to a package name*, not a second install
 * path — the button calls the same gated endpoint the free-form box does. When
 * the app isn't allowed to install (not opted in, not on loopback), the entry
 * still earns its place: it shows the exact command for this environment, right
 * on the card, ready to copy.
 */
function Catalog({
  entries,
  canInstall,
  commandTemplate,
  installing,
  onInstall,
}: {
  entries: CatalogPlugin[];
  canInstall: boolean;
  /** Environment-specific install command, with a `<package>` placeholder. */
  commandTemplate: string | null;
  installing: string | null;
  onInstall: (entry: CatalogPlugin, spec: string, version: string | null) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-0.5">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
          Available
        </h4>
        <p className="text-[11px] text-muted">
          Plugins available in the catalogue.
        </p>
      </div>
      <ul className="flex flex-col gap-3">
        {entries.map((entry) => (
          <CatalogCard
            key={entry.id}
            entry={entry}
            canInstall={canInstall}
            commandTemplate={commandTemplate}
            installing={installing}
            onInstall={onInstall}
          />
        ))}
      </ul>
    </div>
  );
}

/**
 * One catalogue entry.
 *
 * Its own component because each card owns a little state — which source and
 * release to install, whether its install command is revealed, and whether it
 * was just copied — which shouldn't be hoisted into a map keyed by plugin id.
 */
function CatalogCard({
  entry,
  canInstall,
  commandTemplate,
  installing,
  onInstall,
}: {
  entry: CatalogPlugin;
  canInstall: boolean;
  commandTemplate: string | null;
  installing: string | null;
  onInstall: (entry: CatalogPlugin, spec: string, version: string | null) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [source, setSource] = useState<"pypi" | "github">("pypi");
  const [version, setVersion] = useState("");

  const spec = source === "github" && entry.repository ? entry.repository : entry.distribution;
  // Looked up for the card itself, not just the picker: the newest release is
  // worth showing before anyone asks, and it is cached server-side.
  const versions = usePluginVersions(spec);
  // A GitHub release is only installable by its wheel URL, so until the list
  // arrives there is no honest command to show for it.
  const requirement =
    requirementFor(versions.data, version) ?? (source === "pypi" ? entry.distribution : null);
  const command =
    requirement === null
      ? null
      : installCommand(commandTemplate ?? "uv pip install <package>", requirement);

  function pickSource(next: "pypi" | "github") {
    setSource(next);
    setVersion("");
  }

  /**
   * Reveal the command *and* put it on the clipboard in one go.
   *
   * The reveal is what the label promises; the copy is what the user was going
   * to do next anyway. Copying can fail (no clipboard permission, insecure
   * context), so it must not gate showing the command — that would leave the
   * button looking broken when the useful half still worked.
   */
  async function showCommand() {
    setRevealed(true);
    if (command === null) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — the command is on screen either way */
    }
  }

  return (
    <li className="flex flex-col gap-3 rounded-lg border border-border bg-surface/60 p-4">
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
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
            <LatestRelease versions={versions} />
            <button
              type="button"
              onClick={() => setOptionsOpen((open) => !open)}
              aria-expanded={optionsOpen}
              className="inline-flex items-center gap-0.5 hover:text-accent"
            >
              {entry.repository ? "Choose source and version" : "Choose version"}
              <ChevronDown
                size={11}
                className={`transition-transform ${optionsOpen ? "rotate-180" : ""}`}
              />
            </button>
          </div>
        </div>
        <button
          type="button"
          disabled={installing !== null || (source === "github" && versions.data === null)}
          onClick={() =>
            canInstall ? onInstall(entry, spec, version || null) : void showCommand()
          }
          className="shrink-0 rounded border border-accent/30 bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
        >
          {installing === entry.distribution
            ? "Installing…"
            : canInstall
              ? version
                ? `Install ${version}`
                : "Install"
              : "Install command"}
        </button>
      </div>

      {optionsOpen && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded border border-border bg-bg px-2.5 py-2 text-xs">
          {entry.repository && (
            <div className="flex items-center gap-2">
              <span className="text-muted">Source</span>
              <div className="flex overflow-hidden rounded border border-border" role="group">
                {(["pypi", "github"] as const).map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    onClick={() => pickSource(kind)}
                    aria-pressed={source === kind}
                    className={`px-2 py-1 text-[11px] ${
                      source === kind
                        ? "bg-accent/15 font-medium text-accent"
                        : "text-muted hover:bg-surface"
                    }`}
                    data-tooltip={
                      kind === "pypi"
                        ? "By name, from your package index"
                        : "The wheel attached to a GitHub release — for when your index hasn't caught up"
                    }
                  >
                    {kind === "pypi" ? "PyPI" : "GitHub"}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center gap-2">
            <span className="text-muted">Version</span>
            <VersionSelect versions={versions.data} value={version} onChange={setVersion} />
          </div>
          <span className="basis-full text-[11px] text-muted">
            {version
              ? "Pinned to this release: upgrades and Precursor updates keep it until you choose another."
              : source === "pypi"
                ? `Latest asks for at least ${versions.data?.latest ?? "the newest release"}. If your index doesn't carry it yet, the install fails instead of settling for an older one — pick GitHub or another version then.`
                : "Installs the newest release's wheel; upgrade from the Installed list later."}
          </span>
        </div>
      )}

      {/* The command itself, in place. It was previously loaded into the
          free-form box further down the panel, which read as the button having
          done nothing: the label promised a command and the eye had to hunt
          for it. */}
      {!canInstall && revealed && command !== null && (
        <div className="flex items-center gap-2 rounded border border-border bg-bg px-2.5 py-2">
          <code className="min-w-0 flex-1 select-all break-all font-mono text-[11px]">
            {command}
          </code>
          <button
            type="button"
            onClick={() => void showCommand()}
            className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-accent"
            aria-label="Copy the install command"
            data-tooltip={copied ? "Copied" : "Copy"}
          >
            {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
          </button>
        </div>
      )}
    </li>
  );
}

/**
 * Where an installed plugin comes from, whether a newer release exists there,
 * and a way to move to it — or to any other release.
 *
 * The newest release is looked up against the plugin's own source (PyPI or its
 * GitHub repository), never against a version core pins, so a plugin can move
 * on its own cadence. Picking an older release is the way around one that
 * doesn't fit this Precursor.
 */
function PluginVersionRow({
  plugin,
  update,
  canInstall,
  commandTemplate,
  busy,
  upgradedTo,
  onUpgrade,
}: {
  plugin: InstalledPlugin;
  update: PluginUpdate | null;
  canInstall: boolean;
  commandTemplate: string | null;
  busy: boolean;
  /** Set once this plugin was moved to another release this session. */
  upgradedTo: string | null;
  onUpgrade: (version: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState("");
  const source = plugin.source;
  const movable = source !== null && source.kind !== "direct" && canInstall && !upgradedTo;
  // Only fetched once the picker opens: the update row above already knows
  // the newest release, and most people never want another one.
  const versions = usePluginVersions(open ? sourceSpec(source) : null);

  let status: ReactNode = null;
  if (upgradedTo) {
    status = <span className="text-muted">Moved to {upgradedTo} — restart to load it.</span>;
  } else if (update?.update_available && update.latest) {
    status = (
      <span className="inline-flex items-center gap-1 font-medium text-accent">
        <CircleArrowUp size={12} />
        {update.latest} available
      </span>
    );
  } else if (update?.latest) {
    status = (
      <span className="inline-flex items-center gap-1 text-muted">
        <Check size={12} className="text-emerald-500" />
        Up to date
      </span>
    );
  } else if (update?.error) {
    status = (
      <span className="text-muted" data-tooltip={update.error}>
        Couldn't check for updates
      </span>
    );
  }

  if (source === null && status === null) return null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <SourceBadge source={source} />
        {status}
        <span className="flex-1" />
        {movable && update?.update_available && update.latest && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onUpgrade(null)}
            className="shrink-0 rounded border border-accent/30 bg-accent/15 px-2 py-1 text-[11px] font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            {busy ? "Upgrading…" : `Upgrade to ${update.latest}`}
          </button>
        )}
        {movable && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="inline-flex shrink-0 items-center gap-0.5 text-[11px] text-muted hover:text-accent"
          >
            Other version
            <ChevronDown size={11} className={`transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        )}
      </div>

      {open && movable && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-bg px-2.5 py-2 text-xs">
          <VersionSelect
            versions={versions.data}
            value={version}
            onChange={setVersion}
            installed={plugin.version}
            disabled={busy}
          />
          <button
            type="button"
            disabled={
              busy || versions.data === null || (version !== "" && version === plugin.version)
            }
            onClick={() => onUpgrade(version || null)}
            className="shrink-0 rounded border border-accent/30 bg-accent/15 px-2 py-1 text-[11px] font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            {busy ? "Installing…" : "Install this version"}
          </button>
          {versions.loading && <span className="text-[11px] text-muted">Looking up releases…</span>}
          {versions.error && <span className="text-[11px] text-red-500">{versions.error}</span>}
          {version && source?.kind === "pypi" && (
            <span className="basis-full text-[11px] text-muted">
              Choosing a release pins it; “Latest” later lifts the pin.
            </span>
          )}
        </div>
      )}

      {/* With the in-app installer off, the upgrade is still one paste away. */}
      {!canInstall && update?.update_available && update.upgrade_requirement && commandTemplate && (
        <p className="text-[11px] text-muted">
          Upgrade it yourself:{" "}
          <code className="break-all rounded bg-surface px-1 py-0.5">
            {installCommand(commandTemplate, update.upgrade_requirement)}
          </code>
        </p>
      )}
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
 * Takes a package name, a GitHub repository link or any requirement. For the
 * first two it looks the releases up as you type, so the newest version is on
 * screen before you commit and an older one is a pick away.
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
}: {
  env: PluginEnvironment | null;
  pkg: string;
  onPkgChange: (v: string) => void;
  installing: boolean;
  onInstall: (version: string | null) => void;
}) {
  const [version, setVersion] = useState("");
  const typed = pkg.trim();
  const spec = isLookupSpec(typed) ? typed : null;
  const versions = usePluginVersions(spec, { debounceMs: 400 });

  // A release picked for one package means nothing for the next.
  useEffect(() => setVersion(""), [spec]);

  if (env === null) return null;

  const isGithub = /github\.com\//i.test(typed);
  // A repository link isn't itself installable — only the wheel it resolves to
  // is — so the command waits for the lookup rather than showing a broken one.
  const requirement =
    requirementFor(versions.data, version) ?? (isGithub ? null : typed || "<package>");
  const command = installCommand(env.command_template, requirement ?? "<package>");
  const submit = () => {
    if (env.can_install && typed) onInstall(version || null);
  };

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
            if (e.key === "Enter") submit();
          }}
          placeholder="package name or GitHub link, e.g. precursor-kanban"
          aria-label="Package name or GitHub repository link"
          className="min-w-0 flex-1 rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
        {spec !== null && versions.data !== null && (
          <VersionSelect versions={versions.data} value={version} onChange={setVersion} />
        )}
        {env.can_install && (
          <button
            type="button"
            disabled={installing || typed.length === 0 || (isGithub && versions.data === null)}
            onClick={submit}
            className="shrink-0 rounded border border-accent/30 bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            {installing ? "Installing…" : "Install"}
          </button>
        )}
      </div>
      {spec !== null && (
        <p className="text-[11px] text-muted">
          <LatestRelease versions={versions} />
        </p>
      )}
      <p className="text-[11px] text-muted">
        {env.can_install
          ? "Or run it yourself, in Precursor's own environment:"
          : env.reason
            ? `${env.reason} Run it yourself, in Precursor's own environment:`
            : "Run it yourself, in Precursor's own environment:"}{" "}
        <code className="break-all rounded bg-surface px-1 py-0.5">{command}</code>
      </p>
    </div>
  );
}

/**
 * The standing permission for Precursor to run an installer on your behalf.
 *
 * Deliberately at the top of the panel and always visible once it applies,
 * rather than tucked inside the install box and rendered only while off.
 * Installing runs a package's own code with Precursor's privileges, so the
 * consent it represents has to be as easy to withdraw as it was to give — a
 * switch that only appears when it is off can be turned on and never off again.
 */
function InstallPermission({
  env,
  onChange,
}: {
  env: PluginEnvironment | null;
  onChange: (enabled: boolean) => void;
}) {
  // Nothing to consent to where the app could never install anyway (not on
  // loopback, or no installer present) — the panel explains that in context.
  if (env === null || !env.installable_here) return null;

  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-surface/60 p-3">
      <input
        type="checkbox"
        checked={env.can_install}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 shrink-0 accent-accent"
      />
      <span className="min-w-0 flex flex-col gap-0.5">
        <span className="text-xs font-medium">Let Precursor install packages for me</span>
        <span className="text-[11px] text-muted">
          Installing runs the package's own code with Precursor's privileges, so
          this stays off unless you ask for it. Turn it off at any time — the
          commands to install by hand are still shown.
        </span>
      </span>
    </label>
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
