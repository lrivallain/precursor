import {
  Children,
  cloneElement,
  isValidElement,
  type ReactNode,
} from "react";
import { AlertTriangle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

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
        {children || "\u200B"}
      </ReactMarkdown>
    </div>
  );
}
