import { useEffect, useState } from "react";
import { AlertTriangle, Check, Code2, Copy, Image as ImageIcon } from "lucide-react";

interface MermaidBlockProps {
  /** Raw mermaid source as produced inside a ```mermaid fence. */
  code: string;
}

// A single mermaid instance shared across every block. Loaded lazily so the
// (large) library only ships when a diagram is actually rendered.
let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;

function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

async function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then(({ default: mermaid }) => mermaid);
  }
  return mermaidPromise;
}

let renderSeq = 0;

/**
 * Render a ```mermaid fenced block as the actual diagram, with a toggle back to
 * the source and a copy button. Mirrors {@link SvgBlock} so mermaid shows up
 * directly in chat, topics, and agents. The library is imported lazily and the
 * theme follows the app's light/dark mode.
 */
export function MermaidBlock({ code }: MermaidBlockProps) {
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dark = typeof document !== "undefined" && isDark();

  useEffect(() => {
    let cancelled = false;
    setError(null);
    const trimmed = code.trim();
    if (!trimmed) {
      setSvg(null);
      return;
    }
    const id = `mermaid-${(renderSeq += 1)}`;
    loadMermaid()
      .then(async (mermaid) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: isDark() ? "dark" : "default",
        });
        const { svg: rendered } = await mermaid.render(id, trimmed);
        if (!cancelled) {
          setSvg(rendered);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSvg(null);
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
    // Re-render when the source or the active theme changes.
  }, [code, dark]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — ignore */
    }
  };

  const showDiagram = !showSource && svg && !error;

  return (
    <div className="my-2 overflow-hidden rounded-md border border-border bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1 text-xs text-muted">
        <span className="font-mono">Mermaid</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setShowSource((v) => !v)}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-bg"
            title={showSource ? "Show preview" : "Show source"}
          >
            {showSource ? <ImageIcon size={13} /> : <Code2 size={13} />}
            {showSource ? "Preview" : "Code"}
          </button>
          <button
            type="button"
            onClick={copy}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-bg"
            title="Copy mermaid source"
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
      {error && !showSource ? (
        <div className="flex items-start gap-2 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div className="min-w-0">
            <p className="mb-1 font-medium">Couldn't render this diagram.</p>
            <pre className="!my-0 !rounded-none !border-0 overflow-x-auto whitespace-pre-wrap">
              <code>{code}</code>
            </pre>
          </div>
        </div>
      ) : showDiagram ? (
        <div
          className="flex justify-center p-3 [&>svg]:h-auto [&>svg]:max-w-full"
          // Markup comes from mermaid with securityLevel "strict" (scripts and
          // event handlers stripped, HTML labels disabled).
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : (
        <pre className="!my-0 !rounded-none !border-0 overflow-x-auto">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}
