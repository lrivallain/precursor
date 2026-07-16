import { useEffect, useRef, useState } from "react";
import { Archive, Info, Settings as SettingsIcon, User } from "lucide-react";
import { api } from "../lib/api";
import type { Me } from "../lib/types";
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
  onArchive: () => void;
  onAbout: () => void;
}

function PersonaMenuPopover({ anchor, onArchive, onAbout }: PopoverProps) {
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
