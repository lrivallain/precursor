import { useEffect, useState } from "react";
import { Package, Pin } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import type { PluginSource, PluginVersions } from "../lib/types";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { Select } from "./Select";

/**
 * Shared pieces for choosing *which* release of a plugin to install.
 *
 * Plugins version independently of Precursor: each one is listed against the
 * place it installs from — PyPI by name, or a GitHub repository's releases —
 * and the user may pick any release, which is the way around a newest version
 * that doesn't fit this Precursor yet.
 */

/** What to ask `/api/plugins/versions` about for an installed source. */
export function sourceSpec(source: PluginSource | null): string | null {
  if (source === null) return null;
  if (source.kind === "github" && source.repository) {
    return `https://github.com/${source.repository}`;
  }
  if (source.kind === "pypi") return source.distribution;
  return null;
}

/**
 * Whether a typed value is worth a versions lookup: a bare package name or a
 * GitHub link. Anything else (`pkg>=1`, `pkg @ url`) is passed through as is.
 */
export function isLookupSpec(value: string): boolean {
  const v = value.trim();
  return /^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$/.test(v) || /github\.com\//i.test(v);
}

/** The requirement installing `version` ("" = latest) from `versions`. */
export function requirementFor(versions: PluginVersions | null, version: string): string | null {
  if (versions === null) return null;
  if (!version) return versions.latest_requirement;
  return versions.versions.find((v) => v.version === version)?.requirement ?? null;
}

/**
 * `template` with its `<package>` placeholder replaced by `requirement`, quoted
 * for a POSIX shell. Requirements are anything but shell-safe: `pkg>=1.2`
 * pasted bare is a redirect that writes a file called `=1.2`, and
 * `pkg @ https://…` is three arguments.
 */
export function installCommand(template: string, requirement: string): string {
  const safe = requirement === "<package>" || /^[A-Za-z0-9._\-/:+,@]+$/.test(requirement);
  const quoted = safe ? requirement : `'${requirement.replace(/'/g, `'\\''`)}'`;
  return template.replace("<package>", quoted);
}

// Per-session memo on top of the server's cache: toggling a picker open and
// shut shouldn't flash "Looking up…" for a list we already have.
const memo = new Map<string, PluginVersions>();

/**
 * Releases for a package name or GitHub link, fetched as `spec` settles.
 *
 * Debounced for the free-form box, where `spec` changes per keystroke, and
 * guarded against out-of-order answers: only the response for the *current*
 * spec is ever shown.
 */
export function usePluginVersions(
  spec: string | null,
  { debounceMs = 0, refreshKey = 0 }: { debounceMs?: number; refreshKey?: number } = {},
) {
  const [state, setState] = useState<{
    spec: string | null;
    data: PluginVersions | null;
    error: string | null;
    loading: boolean;
  }>({ spec: null, data: null, error: null, loading: false });

  useEffect(() => {
    if (!spec) {
      setState({ spec: null, data: null, error: null, loading: false });
      return;
    }
    const cached = refreshKey === 0 ? memo.get(spec) : undefined;
    if (cached) {
      setState({ spec, data: cached, error: null, loading: false });
      return;
    }
    let cancelled = false;
    setState({ spec, data: null, error: null, loading: true });
    const timer = setTimeout(() => {
      api.plugins
        .versions(spec, refreshKey > 0)
        .then((data) => {
          memo.set(spec, data);
          if (!cancelled) setState({ spec, data, error: null, loading: false });
        })
        .catch((e) => {
          if (!cancelled) {
            setState({
              spec,
              data: null,
              error: apiErrorMessage(e, "Couldn't list versions"),
              loading: false,
            });
          }
        });
    }, debounceMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [spec, debounceMs, refreshKey]);

  // The effect catches up a render late: never hand back the previous spec's
  // answer in the meantime.
  if (state.spec !== spec) {
    return { spec, data: null, error: null, loading: spec !== null };
  }
  return state;
}

/** "Latest 1.2 on PyPI", or where the lookup stands. */
export function LatestRelease({ versions }: { versions: ReturnType<typeof usePluginVersions> }) {
  if (versions.loading) return <span>Looking up releases…</span>;
  if (versions.error) {
    return <span className="text-amber-600 dark:text-amber-400">{versions.error}</span>;
  }
  const data = versions.data;
  if (!data?.latest) return null;
  const where =
    data.source.kind === "github" && data.source.repository
      ? `GitHub (${data.source.repository})`
      : "PyPI";
  return (
    <span>
      Latest <span className="font-mono text-fg">{data.latest}</span> on {where}
    </span>
  );
}

/** "Latest" plus every release, newest first. `""` means latest. */
export function VersionSelect({
  versions,
  value,
  onChange,
  installed,
  disabled,
}: {
  versions: PluginVersions | null;
  value: string;
  onChange: (version: string) => void;
  /** The version already installed, marked in the list. */
  installed?: string | null;
  disabled?: boolean;
}) {
  const options = [
    {
      value: "",
      label: versions?.latest ? `Latest (${versions.latest})` : "Latest",
    },
    ...(versions?.versions ?? []).map((v) => ({
      value: v.version,
      label: [
        v.version,
        v.prerelease ? "pre-release" : null,
        installed && v.version === installed ? "installed" : null,
      ]
        .filter(Boolean)
        .join(" · "),
    })),
  ];
  return (
    <Select
      value={value}
      onChange={onChange}
      options={options}
      disabled={disabled || versions === null}
      ariaLabel="Version to install"
      size="sm"
    />
  );
}

/** Where a plugin comes from, and whether it is pinned there. */
export function SourceBadge({ source }: { source: PluginSource | null }) {
  if (source === null) return null;
  const base =
    "inline-flex shrink-0 items-center gap-1 rounded bg-surface px-1.5 py-0.5 text-[11px] text-muted";
  const pin = source.pinned && (
    <span
      className={base}
      data-tooltip={`Pinned to ${source.specifier.replace(/^==/, "")}: upgrades and Precursor updates keep this version until you choose another.`}
    >
      <Pin size={10} />
      pinned
    </span>
  );
  if (source.kind === "github" && source.repository) {
    return (
      <>
        <a
          href={`https://github.com/${source.repository}/releases`}
          target="_blank"
          rel="noopener noreferrer"
          className={`${base} hover:text-accent`}
          data-tooltip="Installed from this repository's GitHub releases"
        >
          <Github size={10} />
          {source.repository}
        </a>
        {pin}
      </>
    );
  }
  if (source.kind === "pypi") {
    return (
      <>
        <span className={base} data-tooltip="Installed by name from your package index">
          <Package size={10} />
          PyPI
        </span>
        {pin}
      </>
    );
  }
  return (
    <span className={base} data-tooltip="Installed from a path or URL — no releases to upgrade from">
      local
    </span>
  );
}
