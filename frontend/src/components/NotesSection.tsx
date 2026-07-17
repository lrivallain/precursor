import { Check, Eye, Loader2, Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "./Markdown";
import { RefineTextarea } from "./RefineTextarea";
import type { MeetingAttachment } from "../lib/types";

interface Props {
  text: string;
  setText: (text: string) => void;
  /** True while a debounced save is in flight. */
  saving: boolean;
  /** True once the latest edits have been persisted. */
  saved: boolean;
  /** Upload a pasted/dropped file; returns the stored attachment (or null). */
  onUpload: (file: File) => Promise<MeetingAttachment | null>;
  /** Show the rendered preview by default (e.g. once the session is ended). */
  defaultPreview?: boolean;
}

/**
 * Live meeting notes: a Markdown scratch pad with edit/preview modes. The text
 * is owned by the parent (LiveView) so switching tabs never drops in-progress
 * content; edits autosave (debounced) and on session end. Files pasted or
 * dropped are uploaded and inserted as Markdown links/images.
 */
export function NotesSection({ text, setText, saving, saved, onUpload, defaultPreview }: Props) {
  const [mode, setMode] = useState<"edit" | "preview">(defaultPreview ? "preview" : "edit");
  // If the session ends while these notes are open, flip to the rendered view.
  useEffect(() => {
    if (defaultPreview) setMode("preview");
  }, [defaultPreview]);
  const [uploading, setUploading] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const textRef = useRef(text);
  textRef.current = text;

  function insertSnippet(snippet: string): void {
    const el = textareaRef.current;
    const current = textRef.current;
    if (el && el.selectionStart != null) {
      const start = el.selectionStart;
      const end = el.selectionEnd;
      const next = current.slice(0, start) + snippet + current.slice(end);
      textRef.current = next;
      setText(next);
      requestAnimationFrame(() => {
        const pos = start + snippet.length;
        el.focus();
        el.setSelectionRange(pos, pos);
      });
    } else {
      const next = current + (current && !current.endsWith("\n") ? "\n" : "") + snippet;
      textRef.current = next;
      setText(next);
    }
  }

  async function handleFiles(files: File[]): Promise<void> {
    if (files.length === 0) return;
    setUploadError(null);
    setMode("edit");
    for (const file of files) {
      setUploading((n) => n + 1);
      try {
        const att = await onUpload(file);
        if (att) {
          const label = att.original_filename || "attachment";
          insertSnippet(`${att.is_image ? "!" : ""}[${label}](${att.url})\n`);
        } else {
          setUploadError("Upload failed.");
        }
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : "Upload failed.");
      } finally {
        setUploading((n) => n - 1);
      }
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Notes</div>
        <div className="ml-1 flex items-center gap-0.5 text-xs">
          <button
            type="button"
            onClick={() => setMode("edit")}
            className={`inline-flex items-center gap-1 rounded px-2 py-1 ${
              mode === "edit" ? "bg-surface text-text" : "text-muted hover:text-text"
            }`}
          >
            <Pencil size={11} /> Edit
          </button>
          <button
            type="button"
            onClick={() => setMode("preview")}
            className={`inline-flex items-center gap-1 rounded px-2 py-1 ${
              mode === "preview" ? "bg-surface text-text" : "text-muted hover:text-text"
            }`}
          >
            <Eye size={11} /> Preview
          </button>
        </div>
        <div className="ml-auto flex items-center gap-1 text-[11px] text-muted">
          {uploading > 0 ? (
            <>
              <Loader2 size={11} className="animate-spin" /> Uploading {uploading}…
            </>
          ) : saving ? (
            <>
              <Loader2 size={11} className="animate-spin" /> Saving…
            </>
          ) : saved ? (
            <>
              <Check size={11} /> Saved
            </>
          ) : null}
        </div>
      </div>

      {uploadError && (
        <div className="border-b border-border bg-red-500/10 px-3 py-1.5 text-[12px] text-red-500">
          {uploadError}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {mode === "edit" ? (
          <RefineTextarea
            ref={textareaRef}
            value={text}
            onValueChange={setText}
            refineKind="note"
            containerClassName="h-full"
            onPaste={(e) => {
              const files = Array.from(e.clipboardData?.items ?? [])
                .filter((it) => it.kind === "file")
                .map((it) => it.getAsFile())
                .filter((f): f is File => f != null);
              if (files.length > 0) {
                e.preventDefault();
                void handleFiles(files);
              }
            }}
            onDragOver={(e) => {
              if (e.dataTransfer?.types?.includes("Files")) {
                e.preventDefault();
                setDragOver(true);
              }
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              const files = Array.from(e.dataTransfer?.files ?? []);
              if (files.length > 0) {
                e.preventDefault();
                setDragOver(false);
                void handleFiles(files);
              }
            }}
            placeholder="Jot down notes as the meeting goes… (Markdown supported — paste or drop files to attach)"
            className={`h-full min-h-[12rem] w-full resize-none rounded border bg-surface px-3 py-2 font-mono text-[13px] outline-none focus:border-accent ${
              dragOver ? "border-dashed border-accent" : "border-border"
            }`}
          />
        ) : text.trim() ? (
          <Markdown>{text}</Markdown>
        ) : (
          <p className="text-sm text-muted">Nothing to preview yet.</p>
        )}
      </div>
    </div>
  );
}
