import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import { Modal } from "./Modal";
import { api } from "../lib/api";
import type { AppVersion } from "../lib/types";

const REPO_URL = "https://github.com/lrivallain/precursor";

interface Props {
  onClose: () => void;
}

/**
 * "About Precursor" dialog reached from the persona menu. Shows the brand
 * logo, the running app version (with dev commit/build date when present),
 * and a link out to the GitHub repository.
 */
export function AboutModal({ onClose }: Props) {
  const [info, setInfo] = useState<AppVersion | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.system
      .getVersion()
      .then((v) => {
        if (!cancelled) setInfo(v);
      })
      .catch(() => {
        /* version line falls back to "unknown" */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const buildDate = formatBuildDate(info?.build_date ?? null);

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      labelledBy="about-title"
      panelClassName="w-full max-w-sm rounded-xl border border-border bg-bg shadow-lg"
    >
      <div className="flex flex-col items-center gap-4 p-6 text-center">
        <img
          src="/logo.svg"
          alt="Precursor"
          className="h-16 w-16 rounded-2xl"
        />
        <div className="flex flex-col gap-1">
          <h2 id="about-title" className="text-lg font-semibold tracking-tight">
            Precursor
          </h2>
          <p className="text-sm text-muted">
            {info ? `Version ${info.version}` : "Version unknown"}
          </p>
          {(info?.commit || buildDate) && (
            <p className="text-[11px] text-muted">
              {info?.commit && (
                <span className="font-mono">{info.commit}</span>
              )}
              {info?.commit && buildDate && " · "}
              {buildDate && <span>built {buildDate}</span>}
            </p>
          )}
        </div>

        <a
          href={REPO_URL}
          target="_blank"
          rel="noreferrer noopener"
          className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm text-text transition-colors hover:border-accent/50 hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <ExternalLink size={15} className="text-muted" />
          <span>View on GitHub</span>
        </a>
      </div>

      <div className="flex justify-end border-t border-border p-3">
        <button
          type="button"
          onClick={onClose}
          className="rounded border border-border px-3 py-1.5 text-sm hover:bg-surface"
        >
          Close
        </button>
      </div>
    </Modal>
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
