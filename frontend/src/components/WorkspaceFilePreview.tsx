import { useRef } from "react";
import { workspaceRawUrl } from "../lib/api";
import { CANVAS_WORKSPACE_SLUG, parseCanvasRef } from "../lib/canvas";
import { useCanvasFrame } from "../lib/useCanvasFrame";
import { isDrawio, isHtml, isMarkdown, isMermaid, isSvg } from "../lib/workspaceFileTypes";
import { DrawioEditor } from "./DrawioEditor";
import { Markdown } from "./Markdown";
import { MermaidBlock } from "./MermaidBlock";
import { SvgBlock } from "./SvgBlock";

/** The rendered view of a workspace file that has one (see `hasPreview`). */
export function WorkspaceFilePreview({
  slug,
  path,
  content,
  mediaVersion,
  onChange,
  onSave,
}: {
  slug: string;
  path: string;
  content: string;
  mediaVersion: number;
  onChange: (content: string) => void;
  onSave: (content: string) => void;
}) {
  const key = `${path}#${mediaVersion}`;
  if (isMarkdown(path)) {
    return (
      <Markdown className="text-sm leading-relaxed p-6 max-w-3xl">{content || "\u200B"}</Markdown>
    );
  }
  if (isDrawio(path)) {
    return <DrawioEditor key={key} path={path} xml={content} onChange={onChange} onSave={onSave} />;
  }
  if (isHtml(path)) return <HtmlPreview key={key} slug={slug} path={path} version={mediaVersion} />;
  const diagram = isMermaid(path) ? (
    <MermaidBlock code={content} bare />
  ) : isSvg(path) ? (
    <SvgBlock code={content} bare />
  ) : null;
  return diagram && <div className="p-6">{diagram}</div>;
}

/** An HTML page; a canvas among them can hand a question to its conversation. */
function HtmlPreview({ slug, path, version }: { slug: string; path: string; version: number }) {
  const ref = useRef<HTMLIFrameElement>(null);
  useCanvasFrame(ref, slug === CANVAS_WORKSPACE_SLUG ? parseCanvasRef(path) : null);
  return (
    <iframe
      ref={ref}
      title={path}
      src={`${workspaceRawUrl(slug, path)}?v=${version}`}
      sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-modals"
      className="w-full h-full border-0 bg-white"
    />
  );
}
