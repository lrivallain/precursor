import { useEffect, useState } from "react";
import { ArrowUpRight, Bug, Check, Copy, Globe } from "lucide-react";
import { Modal } from "./Modal";
import { api } from "../lib/api";
import type { AppVersion } from "../lib/types";

const REPO_URL = "https://github.com/lrivallain/precursor";
const WEBSITE_URL = "https://precursor.vuptime.io/";
const TAGLINE = "Opinionated approach to work follow-up, built as an AI assistant.";

interface Props {
  onClose: () => void;
}

/**
 * "About Precursor" dialog reached from the persona menu. A branded hero with
 * the logo, name, tagline, and a copyable version badge, followed by quick
 * links out to the project and a small license/build footer.
 */
export function AboutModal({ onClose }: Props) {
  const [info, setInfo] = useState<AppVersion | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.system
      .getVersion()
      .then((v) => {
        if (!cancelled) setInfo(v);
      })
      .catch(() => {
        /* version badge falls back to "unknown" */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(t);
  }, [copied]);

  async function copyVersion() {
    if (!info?.version) return;
    try {
      await navigator.clipboard.writeText(info.version);
      setCopied(true);
    } catch {
      /* clipboard unavailable — no-op */
    }
  }

  const buildDate = formatBuildDate(info?.build_date ?? null);
  const year = new Date().getFullYear();

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      labelledBy="about-title"
      panelClassName="w-full max-w-sm overflow-hidden rounded-2xl border border-border bg-bg shadow-xl"
    >
      {/* Branded hero */}
      <div className="relative flex flex-col items-center gap-3 bg-gradient-to-b from-accent/15 to-transparent px-6 pt-8 pb-6 text-center">
        <img
          src="/logo.svg"
          alt="Precursor"
          className="h-16 w-16 rounded-2xl shadow-lg ring-1 ring-black/5"
        />
        <div className="flex flex-col gap-1">
          <h2 id="about-title" className="text-xl font-semibold tracking-tight">
            Precursor
          </h2>
          <p className="mx-auto max-w-[15rem] text-xs leading-relaxed text-muted">
            {TAGLINE}
          </p>
        </div>

        <button
          type="button"
          onClick={copyVersion}
          disabled={!info?.version}
          data-tooltip={info?.version ? "Copy version" : undefined}
          className="group inline-flex items-center gap-1.5 rounded-full border border-border bg-surface/80 px-3 py-1 text-xs font-medium text-text transition-colors hover:border-accent/50 disabled:cursor-default disabled:opacity-70"
        >
          <span className="font-mono">
            {info ? `v${info.version}` : "version unknown"}
          </span>
          {info?.version &&
            (copied ? (
              <Check size={12} className="text-green-500" />
            ) : (
              <Copy
                size={12}
                className="text-muted transition-colors group-hover:text-accent"
              />
            ))}
        </button>
      </div>

      {/* Quick links */}
      <div className="flex flex-col gap-2 px-5 py-4">
        <LinkRow
          href={WEBSITE_URL}
          label="Website"
          sub="precursor.vuptime.io"
          icon={<Globe size={16} className="text-muted" />}
        />
        <LinkRow
          href={REPO_URL}
          label="Source code"
          sub="github.com/lrivallain/precursor"
          icon={<ArrowUpRight size={16} className="text-muted" />}
        />
        <LinkRow
          href={`${REPO_URL}/issues/new`}
          label="Report an issue"
          sub="Found a bug or have an idea?"
          icon={<Bug size={16} className="text-muted" />}
        />
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-3 text-[11px] text-muted">
        <span>MIT License · © {year}</span>
        {(info?.commit || buildDate) && (
          <span className="truncate font-mono" title="Build details">
            {info?.commit}
            {info?.commit && buildDate && " · "}
            {buildDate}
          </span>
        )}
      </div>
    </Modal>
  );
}

function LinkRow({
  href,
  label,
  sub,
  icon,
}: {
  href: string;
  label: string;
  sub: string;
  icon: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="flex items-center gap-3 rounded-lg border border-border bg-surface/40 px-3 py-2.5 transition-colors hover:border-accent/50 hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface">
        {icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-sm font-medium">{label}</span>
        <span className="truncate text-[11px] text-muted">{sub}</span>
      </span>
    </a>
  );
}

/** Turn a hatch-vcs "dYYYYMMDD" build stamp into a readable date. */
function formatBuildDate(raw: string | null): string | null {
  if (!raw || raw.length !== 8) return raw;
  const y = raw.slice(0, 4);
  const m = raw.slice(4, 6);
  const d = raw.slice(6, 8);
  return `${y}-${m}-${d}`;
}
