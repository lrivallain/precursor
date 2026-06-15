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
        }}
      >
        {children || "\u200B"}
      </ReactMarkdown>
    </div>
  );
}
