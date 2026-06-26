import {
  Children,
  cloneElement,
  isValidElement,
  useState,
  type ReactNode,
} from "react";
import { AlertTriangle, Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { SvgBlock } from "./SvgBlock";

interface MarkdownProps {
  children: string;
  /** Extra classes appended to the `.markdown` wrapper (spacing, sizing…). */
  className?: string;
}

/** Open external (http/https) links in a new tab; keep in-app anchors inline. */
function isExternalHref(href: string | undefined): boolean {
  if (!href) return false;
  return /^https?:\/\//i.test(href);
}

const WARNING_MARKER = /^\s*\[!WARNING\]\s*/i;

function flattenText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (!isValidElement(node)) return "";
  return flattenText((node.props as { children?: ReactNode }).children);
}

function stripWarningMarker(node: ReactNode): ReactNode {
  let stripped = false;

  const walk = (value: ReactNode): ReactNode => {
    if (value === null || value === undefined || typeof value === "boolean") return value;
    if (typeof value === "string" || typeof value === "number") {
      if (stripped) return value;
      const text = String(value);
      const next = text.replace(WARNING_MARKER, () => {
        stripped = true;
        return "";
      });
      return next;
    }
    if (Array.isArray(value)) return value.map(walk);
    if (!isValidElement(value)) return value;
    const nextChildren = Children.map(
      (value.props as { children?: ReactNode }).children,
      walk,
    );
    return cloneElement(value, undefined, nextChildren);
  };

  return walk(node);
}

const FENCE_RE = /(```[\s\S]*?```|~~~[\s\S]*?~~~)/g;
const RAW_SVG_RE = /<svg[\s\S]*?<\/svg>/gi;

/**
 * Wrap standalone `<svg>…</svg>` markup that the model emits as raw text (not in
 * a code fence) into a ```svg fence, so the `pre` override below renders it as
 * an image. Fenced regions are left untouched.
 */
function wrapRawSvg(markdown: string): string {
  if (!markdown.toLowerCase().includes("<svg")) return markdown;
  return markdown
    .split(FENCE_RE)
    .map((part, index) => {
      if (index % 2 === 1) return part; // captured fenced block
      return part.replace(
        RAW_SVG_RE,
        (match) => `\n\n\`\`\`svg\n${match.trim()}\n\`\`\`\n\n`,
      );
    })
    .join("");
}

/** Pull SVG source out of a `<pre>`'s `<code>` child, or null when not SVG. */
function svgFromPre(children: ReactNode): string | null {
  const text = flattenText(children).trim();
  if (/^<svg[\s>]/i.test(text) && /<\/svg>\s*$/i.test(text)) return text;
  return null;
}

/**
 * A fenced code block with a hover "Copy" button so the model's ``` output is
 * easy to lift to the clipboard. Positioned over the (scrollable) `<pre>` so it
 * stays put while long lines scroll horizontally.
 */
function CodeBlock({ children, ...props }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(flattenText(children).replace(/\n$/, ""));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  };
  return (
    <div className="md-code group relative">
      <button
        type="button"
        onClick={copy}
        className="absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded border border-border bg-bg/80 px-1.5 py-0.5 text-[11px] text-muted opacity-0 backdrop-blur transition-opacity hover:text-accent focus:opacity-100 group-hover:opacity-100"
        aria-label="Copy code"
        data-tooltip="Copy code"
      >
        {copied ? (
          <Check size={12} className="text-emerald-500" />
        ) : (
          <Copy size={12} />
        )}
        {copied ? "Copied" : "Copy"}
      </button>
      <pre {...props}>{children}</pre>
    </div>
  );
}

/**
 * Single markdown renderer shared across the app so plugin config and styling
 * stay consistent. GFM (tables, task lists, strikethrough, autolinks) plus
 * syntax highlighting. Visual styling lives in the `.markdown` CSS class.
 */
export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={className ? `markdown ${className}` : "markdown"}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          pre({ children: preChildren, ...props }) {
            const svg = svgFromPre(preChildren);
            if (svg) return <SvgBlock code={svg} />;
            return <CodeBlock {...props}>{preChildren}</CodeBlock>;
          },
          a({ href, children: linkChildren, ...props }) {
            const external = isExternalHref(href);
            return (
              <a
                href={href}
                {...props}
                {...(external
                  ? { target: "_blank", rel: "noopener noreferrer" }
                  : {})}
              >
                {linkChildren}
              </a>
            );
          },
          blockquote({ children: quoteChildren, ...props }) {
            const text = flattenText(quoteChildren);
            if (!WARNING_MARKER.test(text)) {
              return <blockquote {...props}>{quoteChildren}</blockquote>;
            }
            const body = stripWarningMarker(quoteChildren);
            return (
              <div className="my-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-amber-900 dark:text-amber-200">
                <div className="flex items-start gap-2">
                  <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                  <div className="min-w-0 [&>:last-child]:mb-0">{body}</div>
                </div>
              </div>
            );
          },
        }}
      >
        {wrapRawSvg(children) || "\u200B"}
      </ReactMarkdown>
    </div>
  );
}
