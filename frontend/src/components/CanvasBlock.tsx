import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Code2,
  Download,
  ExternalLink,
  Image as ImageIcon,
  Loader2,
  Maximize2,
  Minimize2,
  RefreshCw,
  Shapes,
} from "lucide-react";
import {
  CANVAS_WORKSPACE_SLUG,
  canvasFileName,
  canvasRawUrl,
  MAX_FRAME_HEIGHT,
  type CanvasKind,
  type CanvasRef,
} from "../lib/canvas";
import { fetchCanvasSource, saveCanvasSource } from "../lib/canvasApi";
import { useCanvasFrame, useFrameScrollbarHidden } from "../lib/useCanvasFrame";
import { openWorkspaceFile, workspaceFileUrl } from "../lib/workspaceLink";
import { DrawioEditor } from "./DrawioEditor";
import { MermaidBlock } from "./MermaidBlock";
import { SvgBlock } from "./SvgBlock";
import { CANVAS_COLOR } from "./WorkspaceIcon";

const KIND_LABEL: Record<CanvasKind, string> = {
  html: "Interactive",
  drawio: "draw.io",
  mermaid: "Mermaid",
  svg: "SVG",
};

// Kinds whose source reads well as text; HTML and draw.io open in the workspace.
const HAS_SOURCE_VIEW = new Set<CanvasKind>(["mermaid", "svg"]);

const DEFAULT_FRAME_HEIGHT = 420;
const DRAWIO_HEIGHT = 520;

const ACTION =
  "inline-flex items-center rounded p-1 text-muted hover:bg-surface hover:text-text";

/**
 * A conversation canvas shown inline: an interactive HTML page, an editable
 * draw.io diagram, a Mermaid diagram or an SVG, read from the canvas workspace.
 * The header opens it full screen, in the Files section, or downloads it.
 */
export function CanvasBlock({ canvas }: { canvas: CanvasRef }) {
  const [version, setVersion] = useState(() => Date.now());
  const [expanded, setExpanded] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const reload = useCallback(() => setVersion(Date.now()), []);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const frame = expanded
    ? "fixed inset-4 z-50 flex flex-col shadow-2xl"
    : "my-3 flex flex-col";
  return (
    <>
      {expanded && (
        <div className="fixed inset-0 z-40 bg-black/40" onClick={() => setExpanded(false)} />
      )}
      <div
        className={`${frame} not-prose overflow-hidden rounded-lg border border-border bg-bg`}
        data-canvas={canvas.path}
      >
        <CanvasHeader
          canvas={canvas}
          version={version}
          expanded={expanded}
          showSource={showSource}
          onReload={reload}
          onToggle={() => setExpanded((v) => !v)}
          onToggleSource={() => setShowSource((v) => !v)}
        />
        <div className={expanded ? "min-h-0 flex-1 overflow-auto" : ""}>
          <CanvasBody
            canvas={canvas}
            version={version}
            expanded={expanded}
            showSource={showSource}
          />
        </div>
      </div>
    </>
  );
}

function CanvasHeader({
  canvas,
  version,
  expanded,
  showSource,
  onReload,
  onToggle,
  onToggleSource,
}: {
  canvas: CanvasRef;
  version: number;
  expanded: boolean;
  showSource: boolean;
  onReload: () => void;
  onToggle: () => void;
  onToggleSource: () => void;
}) {
  const toggleLabel = expanded ? "Exit full screen" : "Full screen";
  const sourceLabel = showSource ? "Show the canvas" : "Show the source";
  return (
    <div className="flex items-center gap-2 border-b border-border bg-surface/60 px-2.5 py-1.5">
      <Shapes size={14} className={`shrink-0 ${CANVAS_COLOR}`} />
      <span className="truncate text-[12px] font-medium text-text">{canvas.name}</span>
      <span className="rounded bg-border/60 px-1 text-[10px] uppercase tracking-wide text-muted">
        {KIND_LABEL[canvas.kind]}
      </span>
      <div className="ml-auto flex items-center gap-0.5">
        {HAS_SOURCE_VIEW.has(canvas.kind) && (
          <button type="button" className={ACTION} onClick={onToggleSource} aria-label={sourceLabel} data-tooltip={sourceLabel}>
            {showSource ? <ImageIcon size={13} /> : <Code2 size={13} />}
          </button>
        )}
        <button type="button" className={ACTION} onClick={onReload} aria-label="Reload canvas" data-tooltip="Reload">
          <RefreshCw size={13} />
        </button>
        <a
          className={ACTION}
          href={canvasRawUrl(canvas.path, version)}
          download={canvasFileName(canvas)}
          aria-label="Download canvas"
          data-tooltip="Download"
        >
          <Download size={13} />
        </a>
        <a
          className={ACTION}
          href={workspaceFileUrl(CANVAS_WORKSPACE_SLUG, canvas.path) ?? undefined}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            openWorkspaceFile(CANVAS_WORKSPACE_SLUG, canvas.path);
          }}
          aria-label="Open in the canvas workspace"
          data-tooltip="Open in the canvas workspace"
        >
          <ExternalLink size={13} />
        </a>
        <button type="button" className={ACTION} onClick={onToggle} aria-label={toggleLabel} data-tooltip={toggleLabel}>
          {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
      </div>
    </div>
  );
}

interface CanvasViewProps {
  canvas: CanvasRef;
  version: number;
  expanded: boolean;
  showSource: boolean;
}

function CanvasBody(props: CanvasViewProps) {
  if (props.canvas.kind === "html") return <HtmlCanvas {...props} />;
  return <SourceCanvas {...props} />;
}

/**
 * The page runs same-origin on purpose: through `canvas-sdk.js` it reads the
 * theme, saves its state and may drive Precursor's API.
 */
function HtmlCanvas({ canvas, version, expanded }: CanvasViewProps) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(DEFAULT_FRAME_HEIGHT);

  useCanvasFrame(ref, canvas, setHeight);
  // Past the cap the page needs its own scrollbar, and full screen too.
  useFrameScrollbarHidden(ref, !expanded && height < MAX_FRAME_HEIGHT);

  return (
    <iframe
      ref={ref}
      title={canvas.name}
      src={canvasRawUrl(canvas.path, version)}
      sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-modals"
      className="block w-full border-0 bg-bg"
      style={{ height: expanded ? "100%" : height }}
    />
  );
}

function useCanvasSource(path: string, version: number) {
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    fetchCanvasSource(path, version)
      .then((text) => !cancelled && setSource(text))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [path, version]);
  return { source, error };
}

function SourceCanvas({ canvas, version, expanded, showSource }: CanvasViewProps) {
  const { source, error } = useCanvasSource(canvas.path, version);
  if (error) return <CanvasNotice icon={<AlertTriangle size={14} />}>{error}</CanvasNotice>;
  if (source === null) {
    return <CanvasNotice icon={<Loader2 size={14} className="animate-spin" />}>Loading canvas…</CanvasNotice>;
  }
  if (showSource && HAS_SOURCE_VIEW.has(canvas.kind)) {
    return (
      <pre className="!my-0 !rounded-none !border-0 overflow-x-auto">
        <code>{source}</code>
      </pre>
    );
  }
  if (canvas.kind === "mermaid") return <MermaidBlock code={source} bare />;
  if (canvas.kind === "svg") return <SvgBlock code={source} bare />;
  return <DrawioCanvas path={canvas.path} xml={source} expanded={expanded} />;
}

const DRAWIO_AUTOSAVE_MS = 1500;

/** Edits made in the inline editor are saved back to the canvas as you go. */
function DrawioCanvas({ path, xml, expanded }: { path: string; xml: string; expanded: boolean }) {
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef(xml);
  const save = useCallback(() => {
    window.clearTimeout(timer.current);
    if (latest.current !== xml) void saveCanvasSource(path, latest.current);
  }, [path, xml]);
  useEffect(() => save, [save]);
  return (
    <div style={{ height: expanded ? "100%" : DRAWIO_HEIGHT }}>
      <DrawioEditor
        path={path}
        xml={xml}
        onChange={(next) => {
          latest.current = next;
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(save, DRAWIO_AUTOSAVE_MS);
        }}
        onSave={(next) => {
          latest.current = next;
          save();
        }}
      />
    </div>
  );
}

function CanvasNotice({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-3 py-4 text-[12px] text-muted">
      {icon}
      <span>{children}</span>
    </div>
  );
}
