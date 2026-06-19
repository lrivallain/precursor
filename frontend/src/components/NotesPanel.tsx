import { useEffect, useRef, useState } from "react";
import {
  Loader2,
  MessageSquarePlus,
  NotebookPen,
  Paperclip,
  Save,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { CommandPanel } from "./CommandPanel";
import { api } from "../lib/api";
import type { NoteDraftAttachment } from "../lib/types";

export type NotesAction =
  | "append"
  | "append-and-ask"
  | "post-comment";

interface Props {
  /** True when the topic is linked to a GitHub issue (controls the post button). */
  hasIssue: boolean;
  /** When false, the "Post as comment" (GitHub) action is hidden entirely (chats). */
  allowPostComment?: boolean;
  /** True while a rephrase round-trip is in flight. */
  rephrasing: boolean;
  /** True while one of the terminal actions is in flight. */
  acting: boolean;
  /** True while loading a previously saved draft. */
  loadingDraft?: boolean;
  /** True while persisting the current draft. */
  savingDraft?: boolean;
  error: string | null;
  attachments: NoteDraftAttachment[];
  uploadingAttachments?: number;
  attachmentsError?: string | null;
  onRephrase: (text: string) => void | Promise<void>;
  onSaveDraft?: (text: string) => void | Promise<void>;
  onAction: (action: NotesAction, text: string) => void | Promise<void>;
  onAttachFiles: (files: Iterable<File>) => void | Promise<void>;
  onRemoveAttachment: (id: number) => void | Promise<void>;
  onCancel: (text: string) => void | Promise<void>;
  /** Seed text loaded from the server-side draft store. */
  initialText?: string;
  /**
   * When the parent receives a rephrased text, it pushes it back here so the
   * textarea is updated in place. We use a controlled-ish pattern: the parent
   * owns the seed, this component owns the live edits.
   */
  rephrasedText?: string;
}

export function NotesPanel({
  hasIssue,
  allowPostComment = true,
  rephrasing,
  acting,
  loadingDraft = false,
  savingDraft = false,
  error,
  attachments,
  uploadingAttachments = 0,
  attachmentsError = null,
  onRephrase,
  onSaveDraft,
  onAction,
  onAttachFiles,
  onRemoveAttachment,
  onCancel,
  initialText,
  rephrasedText,
}: Props) {
  const [text, setText] = useState(initialText ?? "");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setText(initialText ?? "");
  }, [initialText]);

  // Apply an AI rephrase result exactly when a rephrase round-trip finishes
  // (rephrasing: true → false), not on every render. Keying on that lifecycle
  // instead of comparing against the live text means the user can freely edit
  // the suggestion afterwards without their keystrokes snapping back to the
  // AI version on the next render.
  const prevRephrasingRef = useRef(rephrasing);
  useEffect(() => {
    const justFinished = prevRephrasingRef.current && !rephrasing;
    prevRephrasingRef.current = rephrasing;
    if (justFinished && !error && rephrasedText !== undefined) {
      setText(rephrasedText);
    }
  }, [rephrasing, error, rephrasedText]);

  const hasAttachments = attachments.length > 0;
  const empty = !text.trim() && !hasAttachments;
  const busy = rephrasing || acting || loadingDraft || savingDraft;

  return (
    <CommandPanel
      icon={<NotebookPen size={14} className="text-accent" />}
      title="Notes"
      subtitle="scratch pad — not posted until you choose an action"
      onClose={() => void onCancel(text)}
      closeLabel="Discard notes"
      body={text}
      onBodyChange={setText}
      onBodyPaste={(e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        const files: File[] = [];
        for (const it of items) {
          if (it.kind === "file") {
            const f = it.getAsFile();
            if (f && f.type.startsWith("image/")) files.push(f);
          }
        }
        if (files.length > 0) {
          e.preventDefault();
          void onAttachFiles(files);
        }
      }}
      bodyPlaceholder="Start typing your notes… (Markdown supported)"
      windowStorageKey="precursor:notesPanel:window"
      previewEmptyHint="Nothing to preview yet."
      disabled={busy}
      error={error}
      bodyTop={
        <div className="flex flex-wrap items-center gap-2">
          {attachments.map((a) => {
            const label = a.original_filename || `image-${a.id}`;
            return (
              <div
                key={a.id}
                className="flex items-center gap-2 pl-1 pr-2 py-1 rounded border border-border bg-surface text-xs max-w-[14rem]"
              >
                <img
                  src={api.noteAttachmentUrl(a.id)}
                  alt=""
                  className="w-8 h-8 rounded object-cover border border-border shrink-0"
                />
                <span className="truncate" title={label}>
                  {label}
                </span>
                <button
                  type="button"
                  onClick={() => void onRemoveAttachment(a.id)}
                  className="p-0.5 rounded text-muted hover:text-text hover:bg-bg shrink-0"
                  aria-label={`Remove ${label}`}
                  data-tooltip="Remove"
                >
                  <X size={12} />
                </button>
              </div>
            );
          })}
          {uploadingAttachments > 0 && (
            <span className="text-[11px] text-muted italic px-2 py-1">
              Uploading {uploadingAttachments}…
            </span>
          )}
          {attachmentsError && (
            <span className="text-[11px] text-red-500 px-2 py-1">{attachmentsError}</span>
          )}
        </div>
      }
      previewBottom={
        attachments.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {attachments.map((a) => (
              <a key={a.id} href={api.noteAttachmentUrl(a.id)} target="_blank" rel="noreferrer">
                <img
                  src={api.noteAttachmentUrl(a.id)}
                  alt={a.original_filename || ""}
                  className="max-w-[16rem] max-h-56 rounded border border-border object-contain bg-bg"
                />
              </a>
            ))}
          </div>
        ) : undefined
      }
      footer={
        <>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) void onAttachFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
          >
            <Paperclip size={14} />
            Attach image
          </button>
          <button
            onClick={() => void onRephrase(text)}
            disabled={!text.trim() || busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
          >
            {rephrasing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {rephrasing ? "Rephrasing…" : "Rephrase with AI"}
          </button>
          {onSaveDraft && (
            <button
              onClick={() => void onSaveDraft(text)}
              disabled={empty || busy}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
            >
              {savingDraft ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              {savingDraft ? "Saving…" : "Save draft"}
            </button>
          )}

          <div className="flex-1" />

          <button
            onClick={() => void onAction("append", text)}
            disabled={empty || busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
            title="Append the notes to the conversation without invoking the assistant."
          >
            <MessageSquarePlus size={14} />
            Add to chat
          </button>
          <button
            onClick={() => void onAction("append-and-ask", text)}
            disabled={empty || busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
            title="Append the notes and ask the assistant to comment on them."
          >
            <Send size={14} />
            Add &amp; ask AI
          </button>
          {allowPostComment && (
            <button
              onClick={() => void onAction("post-comment", text)}
              disabled={empty || busy || !hasIssue}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-accent text-white text-xs disabled:opacity-40"
              title={
                hasIssue
                  ? "Post the notes as a comment on the linked GitHub issue."
                  : "Link a GitHub issue to this topic to enable posting."
              }
            >
              <Github size={14} />
              Post as comment
            </button>
          )}
        </>
      }
    />
  );
}
