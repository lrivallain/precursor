import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ExternalLink, FileText, Maximize, X, ZoomIn, ZoomOut } from "lucide-react";
import { api } from "../lib/api";
import { isTextAttachment } from "../lib/attachments";

interface PreviewAttachment {
  id: number;
  mime: string;
  original_filename: string;
}

export function AttachmentPreview({
  attachment,
  url,
  children,
  className,
}: {
  attachment: PreviewAttachment;
  url: string;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const label = attachment.original_filename || `attachment-${attachment.id}`;
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={className}
        aria-label={`Preview ${label}`}
        data-tooltip={`Preview ${label}`}
        onClick={() => setOpen(true)}
      >
        {children}
      </button>
      {open && createPortal(
        <AttachmentViewer attachment={attachment} url={url} onClose={() => setOpen(false)} />,
        triggerRef.current?.ownerDocument.body ?? document.body,
      )}
    </>
  );
}

function AttachmentViewer({ attachment, url, onClose }: {
  attachment: PreviewAttachment;
  url: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [zoom, setZoom] = useState(1);
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const [viewport, setViewport] = useState({ width: 0, height: 0 });
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const label = attachment.original_filename || `attachment-${attachment.id}`;
  const mime = attachment.mime.toLowerCase().split(";", 1)[0].trim();
  const isImage = mime.startsWith("image/");
  const isPdf = mime === "application/pdf";
  const isText = !isImage && !isPdf && isTextAttachment(attachment);
  const canZoom = (isImage || isText) && !error;
  const fit = imageSize.width && viewport.width
    ? Math.min(1, viewport.width / imageSize.width, viewport.height / imageSize.height)
    : 1;
  const width = imageSize.width * fit * zoom;
  const height = imageSize.height * fit * zoom;

  useEffect(() => {
    const dialog = dialogRef.current!;
    const ownerDocument = dialog.ownerDocument;
    const previousFocus = ownerDocument.activeElement;
    const overflow = ownerDocument.body.style.overflow;
    ownerDocument.body.style.overflow = "hidden";
    dialog.showModal();
    closeRef.current?.focus();
    return () => {
      dialog.close();
      ownerDocument.body.style.overflow = overflow;
      if (previousFocus instanceof ownerDocument.defaultView!.HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);

  useEffect(() => {
    const element = viewportRef.current!;
    const observer = new ResizeObserver(([entry]) => {
      setViewport({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!isText) return;
    const controller = new AbortController();
    api.attachments.readText(url, controller.signal).then(
      (value) => { if (!controller.signal.aborted) setText(value); },
      (reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Could not load attachment.");
        }
      },
    );
    return () => controller.abort();
  }, [isText, url]);

  useEffect(() => {
    if (!canZoom) return;
    const element = viewportRef.current!;
    function onWheel(event: WheelEvent) {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((value) => Math.max(0.25, Math.min(4, value + (event.deltaY < 0 ? 0.25 : -0.25))));
    }
    // React's wheel listeners are passive, so they cannot suppress browser zoom.
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [canZoom]);

  function changeZoom(next: number) {
    setZoom(Math.max(0.25, Math.min(4, next)));
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="fixed inset-0 m-auto h-[88dvh] w-[94vw] max-w-6xl max-h-none rounded-xl border border-border bg-surface p-0 text-text shadow-2xl backdrop:bg-black/60"
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onKeyDown={(event) => {
        // Keep Escape from also dismissing the notes/settings panel underneath.
        event.stopPropagation();
        if (event.key === "Tab") {
          const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], iframe, [tabindex='0']"));
          const first = controls[0];
          const last = controls.at(-1);
          const active = event.currentTarget.ownerDocument.activeElement;
          if (event.shiftKey && active === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && active === last) {
            event.preventDefault();
            first?.focus();
          }
        }
        if (!canZoom) return;
        if (event.key === "+" || event.key === "=") { event.preventDefault(); changeZoom(zoom + 0.25); }
        if (event.key === "-") { event.preventDefault(); changeZoom(zoom - 0.25); }
        if (event.key === "0") { event.preventDefault(); setZoom(1); }
      }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div className="flex h-full flex-col">
        <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2">
          <h2 id={titleId} className="min-w-0 flex-1 truncate text-sm font-medium">{label}</h2>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted hover:bg-bg hover:text-text"
          >
            <ExternalLink size={13} />
            Open in new tab
          </a>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close attachment preview" data-tooltip="Close (Escape)" className="rounded p-1.5 text-muted hover:bg-bg hover:text-text">
            <X size={18} />
          </button>
        </header>
        <div
          ref={viewportRef}
          tabIndex={0}
          aria-label="Attachment content"
          className="relative min-h-0 flex-1 overflow-auto bg-bg overscroll-contain"
        >
          {error ? (
            <p role="alert" className="p-6 text-sm text-red-500">{error} Use “Open in new tab” to access the original file.</p>
          ) : isImage ? (
            <div className="flex items-center justify-center" style={{ width: Math.max(viewport.width, width), height: Math.max(viewport.height, height) }}>
              <img
                src={url}
                alt={label}
                draggable={false}
                className="max-w-none object-contain"
                style={imageSize.width ? { width, height } : { maxWidth: "100%", maxHeight: "100%" }}
                onLoad={(event) => setImageSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
                onError={() => setError("Could not load image preview.")}
              />
            </div>
          ) : isPdf ? (
            <iframe src={url} title={`Preview ${label}`} className="h-full w-full border-0" onError={() => setError("Could not load PDF preview.")} />
          ) : isText ? (
            text === null ? <p role="status" className="p-6 text-sm text-muted">Loading preview…</p> : (
              <>
                {text.length > 200_000 && <p className="px-6 pt-4 text-xs text-muted">Preview limited to the first 200,000 characters. Open the original file for the full content.</p>}
                <pre className="w-max min-w-full p-6 font-mono" style={{ fontSize: `${14 * zoom}px` }}>{text.slice(0, 200_000)}</pre>
              </>
            )
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-muted">
              <FileText size={36} />
              <p className="text-sm">This file type cannot be previewed in the app.</p>
              <p className="text-xs">Use “Open in new tab” to open or download the original file.</p>
            </div>
          )}
        </div>
        {canZoom && (
          <footer className="flex shrink-0 items-center justify-center gap-2 border-t border-border px-3 py-2">
            <button type="button" onClick={() => changeZoom(zoom - 0.25)} disabled={zoom <= 0.25} aria-label="Zoom out" data-tooltip="Zoom out" className="rounded p-1.5 hover:bg-bg disabled:opacity-40"><ZoomOut size={18} /></button>
            <output className="w-14 text-center text-xs tabular-nums" aria-label="Zoom level">{Math.round(zoom * 100)}%</output>
            <button type="button" onClick={() => changeZoom(zoom + 0.25)} disabled={zoom >= 4} aria-label="Zoom in" data-tooltip="Zoom in" className="rounded p-1.5 hover:bg-bg disabled:opacity-40"><ZoomIn size={18} /></button>
            <button type="button" onClick={() => setZoom(1)} aria-label={isImage ? "Fit image to window" : "Reset zoom"} data-tooltip={isImage ? "Fit to window" : "Reset zoom"} className="inline-flex items-center gap-1 rounded px-2 py-1.5 text-xs hover:bg-bg"><Maximize size={16} />{isImage ? "Fit" : "Reset"}</button>
          </footer>
        )}
      </div>
    </dialog>
  );
}
