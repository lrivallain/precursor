import { useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, Columns2, Loader2 } from "lucide-react";
import { useResizableWidth } from "../../lib/useResizableWidth";
import { ResizeHandle } from "../ResizeHandle";

type Tab = "work" | "discussion";

/**
 * The arrow from the discussion to the work pane: it moves the model's latest
 * proposal across. It stays dimmed (with `idleReason` as its hint) until a reply
 * actually proposes something, and resolves true once the proposal landed.
 */
export type Transfer = {
  label: string;
  tooltip: string;
  idleReason: string;
  ready: boolean;
  onClick: () => Promise<boolean | void>;
};

const SPLIT_KEY = "precursor.steps.split";

function readSplit(): boolean {
  try {
    return window.localStorage.getItem(SPLIT_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * The artifact (or final output) and its discussion, as tabs or — like a live
 * session — side by side with a draggable divider. Both panes stay mounted so
 * switching never drops an unsaved draft; the discussion mounts on first view
 * because opening it creates the chat.
 */
export function WorkTabs({
  workLabel,
  workIcon,
  work,
  discussion,
  ariaLabel,
  transfer,
}: {
  workLabel: string;
  workIcon: ReactNode;
  work: ReactNode;
  discussion: ReactNode;
  ariaLabel: string;
  transfer?: Transfer;
}) {
  const [transferring, setTransferring] = useState(false);
  const [tab, setTab] = useState<Tab>("work");
  const [split, setSplit] = useState(readSplit);
  const [seen, setSeen] = useState(false);
  const { width, onMouseDown } = useResizableWidth({
    storageKey: "precursor.steps.discussionWidth",
    defaultWidth: 440,
    min: 280,
    max: 900,
    side: "left",
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(SPLIT_KEY, split ? "1" : "0");
    } catch {
      // storage unavailable: the choice just won't persist
    }
  }, [split]);

  const showDiscussion = split || tab === "discussion";
  useEffect(() => {
    if (showDiscussion) setSeen(true);
  }, [showDiscussion]);

  async function runTransfer(): Promise<void> {
    if (!transfer || !transfer.ready || transferring) return;
    setTransferring(true);
    try {
      const landed = await transfer.onClick();
      if (landed && !split) setTab("work");
    } finally {
      setTransferring(false);
    }
  }

  const tabs: [Tab, string, ReactNode][] = [
    ["work", workLabel, workIcon],
    ["discussion", "Discussion", null],
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center gap-1 border-b border-border" role="tablist" aria-label={ariaLabel}>
        {!split &&
          tabs.map(([id, label, icon]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm ${
                tab === id
                  ? "border-accent font-medium text-accent"
                  : "border-transparent text-muted hover:text-fg"
              }`}
            >
              {icon} {label}
            </button>
          ))}
        {split && <span className="px-3 py-2 text-sm text-muted">{workLabel} · Discussion</span>}
        <button
          type="button"
          onClick={() => setSplit((v) => !v)}
          aria-pressed={split}
          aria-label={split ? "Merge into tabs" : "Show side by side"}
          data-tooltip={split ? "Merge into tabs" : "Show the discussion beside the artifact"}
          className="ml-auto hidden rounded p-1 text-muted hover:bg-surface hover:text-accent lg:block"
        >
          <Columns2 size={14} className={split ? "text-accent" : ""} />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row lg:gap-0">
        <div
          className={`min-w-0 flex-1 lg:overflow-y-auto ${!split && tab !== "work" ? "hidden" : ""} ${split ? "lg:pr-4" : ""}`}
        >
          {work}
        </div>
        {split && (
          <div className="relative hidden w-px shrink-0 bg-border lg:block">
            <ResizeHandle onMouseDown={onMouseDown} side="right" />
            {transfer && (
              <button
                type="button"
                onClick={() => void runTransfer()}
                disabled={transferring || !transfer.ready}
                aria-label={transfer.label}
                data-tooltip={transfer.ready ? transfer.tooltip : transfer.idleReason}
                className={`absolute left-1/2 top-24 z-10 flex h-12 w-12 -translate-x-1/2 items-center justify-center rounded-full shadow-lg ring-4 ring-bg transition ${
                  transfer.ready
                    ? "bg-accent text-white hover:scale-105"
                    : "cursor-not-allowed bg-surface text-muted/60 shadow-none"
                }`}
              >
                {transferring ? <Loader2 size={22} className="animate-spin" /> : <ArrowLeft size={24} strokeWidth={2.5} />}
              </button>
            )}
          </div>
        )}
        {seen && (
          <div
            className={`min-h-[28rem] min-w-0 overflow-hidden rounded-lg border border-border ${
              split ? "lg:ml-0 lg:min-h-0 lg:w-[var(--discussion-w)] lg:shrink-0 lg:rounded-none lg:border-0 lg:pl-4" : ""
            } ${showDiscussion ? "" : "hidden"} ${split ? "" : "flex-1"}`}
            style={split ? ({ "--discussion-w": `${width}px` } as React.CSSProperties) : undefined}
          >
            <div className="flex h-full min-h-0 flex-col">
              <div className="min-h-0 flex-1">{discussion}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
