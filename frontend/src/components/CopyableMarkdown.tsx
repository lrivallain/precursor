import { useRef, useState } from "react";
import { Check, Code2, Copy } from "lucide-react";
import { copyFormatted } from "../lib/emailHtml";
import { Markdown } from "./Markdown";

interface Props {
  children: string;
  /** Extra classes forwarded to the underlying `.markdown` wrapper. */
  className?: string;
}

/**
 * Rendered markdown with a hover toolbar to copy the content as email-safe
 * HTML (formatting preserved when pasted into docs/email) or as its markdown source
 * — mirroring the copy affordances on chat/topic assistant answers.
 */
export function CopyableMarkdown({ children, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState<null | "html" | "md">(null);

  function flash(kind: "html" | "md"): void {
    setCopied(kind);
    window.setTimeout(() => setCopied(null), 1400);
  }

  async function copyHtml(): Promise<void> {
    const root = ref.current?.querySelector(".markdown");
    if (root && (await copyFormatted(root, children))) flash("html");
  }

  async function copySource(): Promise<void> {
    try {
      await navigator.clipboard.writeText(children);
      flash("md");
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  }

  return (
    <div className="group relative">
      <div className="hover-reveal absolute right-0 top-0 z-10 flex items-center gap-0.5 rounded-full border border-border bg-surface/90 px-0.5 py-0.5 opacity-0 backdrop-blur transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        <button
          type="button"
          onClick={() => void copyHtml()}
          className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] text-muted hover:text-accent"
          aria-label="Copy formatted (HTML)"
          data-tooltip="Copy (formatted)"
        >
          {copied === "html" ? (
            <Check size={12} className="text-emerald-500" />
          ) : (
            <Copy size={12} />
          )}
          {copied === "html" ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          onClick={() => void copySource()}
          className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] text-muted hover:text-accent"
          aria-label="Copy markdown source"
          data-tooltip="Copy source"
        >
          {copied === "md" ? (
            <Check size={12} className="text-emerald-500" />
          ) : (
            <Code2 size={12} />
          )}
          Source
        </button>
      </div>
      <div ref={ref}>
        <Markdown className={className}>{children}</Markdown>
      </div>
    </div>
  );
}
