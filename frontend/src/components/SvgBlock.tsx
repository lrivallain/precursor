import { useMemo, useState } from "react";
import { Check, Code2, Copy, Image as ImageIcon } from "lucide-react";

interface SvgBlockProps {
  /** Raw `<svg>…</svg>` source as produced by the model. */
  code: string;
}

/**
 * Strip script-y vectors from model-generated SVG before we inject it with
 * `dangerouslySetInnerHTML`: drop `<script>`/`<foreignObject>`, inline event
 * handlers, and `javascript:` URLs. Returns null when the markup can't be
 * parsed as SVG so callers fall back to showing the source.
 */
function sanitizeSvg(raw: string): string | null {
  if (typeof window === "undefined" || typeof DOMParser === "undefined") {
    return null;
  }
  const doc = new DOMParser().parseFromString(raw, "image/svg+xml");
  if (doc.querySelector("parsererror")) return null;
  const svg = doc.documentElement;
  if (!svg || svg.tagName.toLowerCase() !== "svg") return null;

  const walk = (el: Element) => {
    const tag = el.tagName.toLowerCase();
    if (tag === "script" || tag === "foreignobject") {
      el.remove();
      return;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.replace(/\s+/g, "").toLowerCase();
      if (name.startsWith("on")) {
        el.removeAttribute(attr.name);
      } else if (
        (name === "href" || name === "xlink:href" || name === "src") &&
        value.startsWith("javascript:")
      ) {
        el.removeAttribute(attr.name);
      }
    }
    for (const child of Array.from(el.children)) walk(child);
  };
  walk(svg);

  // Inline SVGs that only declare a viewBox (no width/height) collapse to zero
  // size as a flex item — backfill intrinsic dimensions so they actually show.
  const viewBox = svg.getAttribute("viewBox");
  if ((!svg.hasAttribute("width") || !svg.hasAttribute("height")) && viewBox) {
    const parts = viewBox.trim().split(/[\s,]+/).map(Number);
    if (parts.length === 4 && parts.every(Number.isFinite)) {
      const [, , vbWidth, vbHeight] = parts;
      if (!svg.hasAttribute("width") && vbWidth > 0) {
        svg.setAttribute("width", String(vbWidth));
      }
      if (!svg.hasAttribute("height") && vbHeight > 0) {
        svg.setAttribute("height", String(vbHeight));
      }
    }
  }

  return new XMLSerializer().serializeToString(svg);
}

/**
 * Render model-authored SVG markup as the actual image instead of a code block,
 * with a toggle back to the source and a copy button. Used by the shared
 * Markdown renderer so SVG shows up directly in chat, topics, and agents.
 */
export function SvgBlock({ code }: SvgBlockProps) {
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);

  const sanitized = useMemo(() => sanitizeSvg(code), [code]);

  // Unparseable markup: behave like a normal code block.
  if (!sanitized) {
    return (
      <pre>
        <code>{code}</code>
      </pre>
    );
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — ignore */
    }
  };

  return (
    <div className="my-2 overflow-hidden rounded-md border border-border bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1 text-xs text-muted">
        <span className="font-mono">SVG</span>
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
            title="Copy SVG source"
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
      {showSource ? (
        <pre className="!my-0 !rounded-none !border-0 overflow-x-auto">
          <code>{code}</code>
        </pre>
      ) : (
        <div
          className="flex justify-center p-3 [&>svg]:h-auto [&>svg]:max-w-full"
          // Markup is sanitized above (script/handlers/js: URLs removed).
          dangerouslySetInnerHTML={{ __html: sanitized }}
        />
      )}
    </div>
  );
}
