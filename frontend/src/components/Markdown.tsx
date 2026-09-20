import {
  Children,
  cloneElement,
  createContext,
  isValidElement,
  memo,
  useContext,
  useState,
  type ReactElement,
  type ReactNode,
  type InputHTMLAttributes,
} from "react";
import { AlertTriangle, Check, Copy } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import type { PluggableList } from "unified";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { SvgBlock } from "./SvgBlock";
import { MermaidBlock } from "./MermaidBlock";
import { makeHighlightRehype, useSearchHighlight } from "../lib/searchHighlight";

interface MarkdownProps {
  children: string;
  /** Extra classes appended to the `.markdown` wrapper (spacing, sizing…). */
  className?: string;
  /** Opt-in task editing; ordinary transcript Markdown remains read-only. */
  onTaskChange?: (markdown: string) => void;
  tasksDisabled?: boolean;
}

interface TaskListOptions {
  source: string;
  rendered: string;
  insertions: { offset: number; length: number }[];
  onChange: (markdown: string) => void;
  disabled: boolean;
}

const TaskListContext = createContext<TaskListOptions | null>(null);

// Stable component identity keeps keyboard focus on a checkbox across saves.
const TaskListItem: NonNullable<Components["li"]> = ({ node, children, ...props }) => {
  const tasks = useContext(TaskListContext);
  const start = node?.position?.start.offset;
  const marker = !tasks || start == null
    ? null
    : tasks.rendered.slice(start).match(/^((?:[-+*]|\d+[.)])[ \t]+\[)[ xX]\]/);
  if (!tasks || start == null || !marker || !props.className?.includes("task-list-item")) {
    return <li {...props}>{children}</li>;
  }
  const { source, onChange, disabled, insertions } = tasks;
  const renderedOffset = start + marker[1].length;
  const offset = renderedOffset - insertions.reduce(
    (sum, insertion) => sum + (insertion.offset <= renderedOffset ? insertion.length : 0), 0,
  );
  if (!/^\[[ xX]\]$/.test(source.slice(offset - 1, offset + 2))) {
    return <li {...props}>{children}</li>;
  }
  const ownChildren = Children.toArray(children).filter(
    (child) => !isValidElement(child) || (child.type !== "ul" && child.type !== "ol"),
  );
  const firstParagraph = ownChildren.find((child) => isValidElement(child) && child.type === "p");
  const label = flattenText(firstParagraph ?? ownChildren).trim() || "Task";
  function activate(value: ReactNode): ReactNode {
    if (!isValidElement(value)) return value;
    const element = value as ReactElement<{ children?: ReactNode; type?: string }>;
    // Each nested list item owns its own source position.
    if (element.type === "ul" || element.type === "ol") return value;
    if (element.type === "input" && element.props.type === "checkbox") {
      const input = cloneElement(value as ReactElement<InputHTMLAttributes<HTMLInputElement>>, {
        disabled,
        "aria-label": label,
        onChange: (event) => onChange(
          source.slice(0, offset) + (event.currentTarget.checked ? "x" : " ") + source.slice(offset + 1),
        ),
      });
      return <label className="markdown-task-toggle">{input}</label>;
    }
    return cloneElement(element, undefined, Children.map(element.props.children, activate));
  }
  return <li {...props}>{Children.map(children, activate)}</li>;
};

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
function wrapRawSvg(markdown: string, insertions: { offset: number; length: number }[] = []): string {
  if (!markdown.toLowerCase().includes("<svg")) return markdown;
  let sourceOffset = 0;
  let added = 0;
  return markdown
    .split(FENCE_RE)
    .map((part, index) => {
      const start = sourceOffset;
      sourceOffset += part.length;
      if (index % 2 === 1) return part; // captured fenced block
      return part.replace(RAW_SVG_RE, (match, offset: number) => {
        const prefix = "\n\n```svg\n";
        const suffix = "\n```\n\n";
        // Task offsets must still address the source, not these injected fences.
        insertions.push({ offset: start + offset + added, length: prefix.length });
        added += prefix.length;
        insertions.push({ offset: start + offset + match.length + added, length: suffix.length });
        added += suffix.length;
        return prefix + match + suffix;
      });
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
 * Pull mermaid source out of a `<pre>`'s `<code class="language-mermaid">`
 * child, or null when the fence isn't tagged as mermaid.
 */
function mermaidFromPre(children: ReactNode): string | null {
  const code = Children.toArray(children).find(
    (child): child is ReactElement<{ className?: string; children?: ReactNode }> =>
      isValidElement(child),
  );
  if (!code) return null;
  const className = code.props.className ?? "";
  if (!/\blanguage-mermaid\b/.test(className)) return null;
  const text = flattenText(code.props.children).replace(/\n$/, "");
  return text.trim() ? text : null;
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
        className="hover-reveal absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded border border-border bg-bg/80 px-1.5 py-0.5 text-[11px] text-muted opacity-0 backdrop-blur transition-opacity hover:text-accent focus:opacity-100 group-hover:opacity-100"
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
 *
 * Memoized on its props so that re-renders of a parent (e.g. the chat panel
 * re-rendering on every composer keystroke) don't re-parse the markdown and
 * re-run syntax highlighting for the whole transcript — that synchronous work
 * caused visible layout thrash / scrollbar flicker on content-heavy topics.
 */
export const Markdown = memo(function Markdown({
  children, className, onTaskChange, tasksDisabled = false,
}: MarkdownProps) {
  // A non-empty highlight term (set when a content-search hit is opened) adds a
  // rehype pass that wraps matches in <mark>. Kept off the plugin list entirely
  // when idle so normal rendering pays nothing.
  const highlight = useSearchHighlight();
  const insertions: { offset: number; length: number }[] = [];
  const rendered = wrapRawSvg(children, insertions);
  const rehypePlugins: PluggableList = highlight.trim()
    ? [
        [rehypeHighlight, { detect: true, ignoreMissing: true }],
        makeHighlightRehype(highlight),
      ]
    : [[rehypeHighlight, { detect: true, ignoreMissing: true }]];
  return (
    <div className={className ? `markdown ${className}` : "markdown"}>
      <TaskListContext.Provider value={onTaskChange ? {
        source: children, rendered, insertions, onChange: onTaskChange, disabled: tasksDisabled,
      } : null}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={rehypePlugins}
        components={{
          li: onTaskChange ? TaskListItem : "li",
          pre({ children: preChildren, ...props }) {
            const mermaid = mermaidFromPre(preChildren);
            if (mermaid) return <MermaidBlock code={mermaid} />;
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
        {rendered || "\u200B"}
      </ReactMarkdown>
      </TaskListContext.Provider>
    </div>
  );
});
