import { useEffect, useRef, useState } from "react";
import { Archive, Info, Settings as SettingsIcon, Sparkles, User } from "lucide-react";
import { api } from "../lib/api";
import type { CopilotQuota, Me } from "../lib/types";
import { AboutModal } from "./AboutModal";

interface Props {
  collapsed?: boolean;
  onOpenSettings: () => void;
  onOpenArchive: () => void;
}

export function PersonaMenu({ collapsed = false, onOpenSettings, onOpenArchive }: Props) {
  const [me, setMe] = useState<Me | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [quota, setQuota] = useState<CopilotQuota | null>(null);
  const [quotaLoading, setQuotaLoading] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.me.get()
      .then((m) => {
        if (!cancelled) setMe(m);
      })
      .catch(() => {
        if (!cancelled) setMe({ github: null, github_token_source: "none" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Close on outside click / Escape while the popover is open.
  useEffect(() => {
    if (!menuOpen) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  // Lazily pull Copilot credit usage only once the connected user opens the
  // menu — keeps it off the initial render and off the network for guests. The
  // backend caches briefly, so refetching on each open stays cheap and fresh.
  const connected = !!me?.github;
  useEffect(() => {
    if (!menuOpen || !connected) return;
    let cancelled = false;
    setQuotaLoading(true);
    api.me
      .copilot()
      .then((q) => {
        if (!cancelled) setQuota(q);
      })
      .catch(() => {
        if (!cancelled) setQuota(null);
      })
      .finally(() => {
        if (!cancelled) setQuotaLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [menuOpen, connected]);

  const label = me?.github?.name || me?.github?.login || "Guest";
  const sub = me?.github
    ? `@${me.github.login}`
    : me?.github_token_source === "none"
      ? "Not connected"
      : "Connecting…";

  // Three-state connectivity indicator surfaced next to the avatar.
  // - "ok":   identity resolved → GitHub auth works and models are available
  // - "warn": a token is configured but identity is not (yet) resolved
  // - "off":  no token at all
  const ghState: "ok" | "warn" | "off" = me?.github
    ? "ok"
    : me?.github_token_source && me.github_token_source !== "none"
      ? "warn"
      : "off";
  const ghDotClass =
    ghState === "ok"
      ? "bg-green-500"
      : ghState === "warn"
        ? "bg-amber-500"
        : "bg-muted/60";
  const ghTitle =
    ghState === "ok"
      ? `GitHub connected (auth + models) — @${me?.github?.login}`
      : ghState === "warn"
        ? "GitHub token configured but identity unavailable"
        : "GitHub not connected — using mock provider";

  function chooseArchive() {
    setMenuOpen(false);
    onOpenArchive();
  }

  function chooseAbout() {
    setMenuOpen(false);
    setAboutOpen(true);
  }

  if (collapsed) {
    return (
      <div ref={rootRef} className="relative flex flex-col items-center gap-1">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          data-tooltip={`${label} — ${ghTitle}`}
          aria-label={`Open user menu — ${ghTitle}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className="relative inline-block shrink-0 rounded-full hover:ring-2 hover:ring-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/60"
        >
          <Avatar url={me?.github?.avatar_url ?? null} alt={label} />
          <span
            aria-hidden="true"
            className={`absolute -bottom-0.5 -right-0.5 block w-2.5 h-2.5 rounded-full ring-2 ring-bg ${ghDotClass}`}
          />
        </button>
        <button
          type="button"
          onClick={onOpenSettings}
          aria-label="Open settings"
          data-tooltip="Settings"
          className="p-1.5 rounded hover:bg-surface text-muted hover:text-text"
        >
          <SettingsIcon size={16} />
        </button>
        {menuOpen && (
          <PersonaMenuPopover
            anchor="collapsed"
            quota={quota}
            quotaLoading={quotaLoading}
            connected={connected}
            onArchive={chooseArchive}
            onAbout={chooseAbout}
          />
        )}
        {aboutOpen && <AboutModal onClose={() => setAboutOpen(false)} />}
      </div>
    );
  }

  return (
    <div ref={rootRef} className="relative flex items-center gap-1 w-full">
      <button
        type="button"
        onClick={() => setMenuOpen((v) => !v)}
        data-tooltip={`${label} — ${ghTitle}`}
        aria-label={`Open user menu — ${ghTitle}`}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        className="flex items-center gap-2 flex-1 min-w-0 px-2 py-1.5 rounded text-left hover:bg-surface focus:outline-none focus:bg-surface"
      >
        <span className="relative inline-block shrink-0">
          <Avatar url={me?.github?.avatar_url ?? null} alt={label} />
          <span
            aria-hidden="true"
            className={`absolute -bottom-0.5 -right-0.5 block w-2.5 h-2.5 rounded-full ring-2 ring-bg ${ghDotClass}`}
          />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm truncate">{label}</span>
          <span className="block text-[11px] text-muted truncate">{sub}</span>
        </span>
      </button>
      <button
        type="button"
        onClick={onOpenSettings}
        aria-label="Open settings"
        data-tooltip="Settings"
        className="p-2 rounded hover:bg-surface text-muted hover:text-text shrink-0"
      >
        <SettingsIcon size={16} />
      </button>
      {menuOpen && (
        <PersonaMenuPopover
          anchor="expanded"
          quota={quota}
          quotaLoading={quotaLoading}
          connected={connected}
          onArchive={chooseArchive}
          onAbout={chooseAbout}
        />
      )}
      {aboutOpen && <AboutModal onClose={() => setAboutOpen(false)} />}
    </div>
  );
}

interface PopoverProps {
  anchor: "expanded" | "collapsed";
  quota: CopilotQuota | null;
  quotaLoading: boolean;
  connected: boolean;
  onArchive: () => void;
  onAbout: () => void;
}

function PersonaMenuPopover({
  anchor,
  quota,
  quotaLoading,
  connected,
  onArchive,
  onAbout,
}: PopoverProps) {
  // Expanded sidebar: popover floats above the persona row, anchored to its
  // left edge. Collapsed sidebar: it sits to the right of the rail so it does
  // not get clipped by the narrow column.
  const position =
    anchor === "expanded"
      ? "bottom-full mb-2 left-0 right-0"
      : "left-full ml-2 bottom-0 w-56";
  return (
    <div
      role="menu"
      aria-label="User menu"
      className={`absolute ${position} z-40 rounded-md border border-border bg-bg shadow-lg py-1 text-sm`}
    >
      {connected && (
        <CopilotUsage quota={quota} loading={quotaLoading} />
      )}
      <button
        type="button"
        role="menuitem"
        onClick={onArchive}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface"
      >
        <Archive size={14} className="text-muted" />
        <span>Archives</span>
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={onAbout}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface"
      >
        <Info size={14} className="text-muted" />
        <span>About</span>
      </button>
    </div>
  );
}

/**
 * Copilot "AI credits" usage for the connected account: a labelled progress bar
 * plus the next reset date. Hidden entirely when the account has no metered
 * quota (no Copilot seat); unlimited plans show a badge instead of a bar.
 */
function CopilotUsage({
  quota,
  loading,
}: {
  quota: CopilotQuota | null;
  loading: boolean;
}) {
  // Nothing to show once we know there is no metered allowance. While the first
  // fetch is in flight we render a slim skeleton so the menu does not jump.
  if (!quota && loading) {
    return (
      <div className="px-3 pt-2 pb-3 border-b border-border">
        <div className="flex items-center gap-1.5 text-[11px] text-muted mb-2">
          <Sparkles size={12} />
          <span>AI credits</span>
        </div>
        <div className="h-1.5 rounded-full bg-surface animate-pulse" />
      </div>
    );
  }
  if (!quota) return null;

  const resetLabel = formatResetDate(quota.reset_date);
  return (
    <div className="px-3 pt-2 pb-3 border-b border-border">
      <div className="flex items-center justify-between gap-2 text-[11px] mb-1.5">
        <span className="flex items-center gap-1.5 text-muted">
          <Sparkles size={12} />
          <span>AI credits</span>
        </span>
        <span className="tabular-nums text-text">
          {quota.unlimited ? "Unlimited" : `${quota.percent_used}% used`}
        </span>
      </div>
      {!quota.unlimited && (
        <div
          className="h-1.5 rounded-full bg-surface overflow-hidden"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={quota.percent_used}
          aria-label="AI credits used"
        >
          <div
            className={`h-full rounded-full ${usageBarClass(quota.percent_used)}`}
            style={{ width: `${Math.max(0, Math.min(100, quota.percent_used))}%` }}
          />
        </div>
      )}
      {resetLabel && (
        <div className="mt-1.5 text-[11px] text-muted">Resets {resetLabel}</div>
      )}
    </div>
  );
}

// Warm the bar as the allowance runs low so a near-empty balance reads at a
// glance: accent normally, amber past 75%, red past 90%.
function usageBarClass(percentUsed: number): string {
  if (percentUsed >= 90) return "bg-red-500";
  if (percentUsed >= 75) return "bg-amber-500";
  return "bg-accent";
}

// "2026-09-01" → "Sep 1, 2026". Parse the parts as a local date so the day does
// not slip a step in negative UTC offsets. Falls back to the raw string.
function formatResetDate(value: string | null): string | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function Avatar({ url, alt }: { url: string | null; alt: string }) {
  if (url) {
    return (
      <img
        src={url}
        alt={alt}
        className="w-7 h-7 rounded-full border border-border shrink-0"
      />
    );
  }
  return (
    <span className="w-7 h-7 rounded-full bg-bg border border-border flex items-center justify-center text-muted shrink-0">
      <User size={14} />
    </span>
  );
}
