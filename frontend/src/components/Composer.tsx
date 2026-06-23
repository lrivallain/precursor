import { useEffect, useRef, useState } from "react";
import { Mic, Paperclip, Send, StopCircle, X } from "lucide-react";
import { api } from "../lib/api";
import { ATTACHMENT_ACCEPT } from "../lib/attachments";
import type { SlashCommand } from "../lib/commands";
import type { Attachment } from "../lib/types";
import { SlashCommandPicker } from "./SlashCommandPicker";

export interface ComposerSpeech {
  supported: boolean;
  listening: boolean;
  error: string | null;
  toggle: () => void;
}

export interface ComposerAttachments {
  pending: Attachment[];
  uploadingCount: number;
  error: string | null;
  onFiles: (files: Iterable<File>) => void;
  onRemove: (id: number) => void;
}

interface Props {
  value: string;
  onChange: (next: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  /** Slash-command suggestions for the autocomplete picker. */
  suggestions: SlashCommand[];
  /** Prior user messages, newest last — for ↑/↓ history recall. */
  userHistory: string[];
  speech: ComposerSpeech;
  /** Interim (in-progress) dictation transcript, shown while listening. */
  interimText: string;
  height: number;
  onResizeStart: (e: React.MouseEvent) => void;
  /** When provided, the composer supports attachments (paperclip / paste / drop). */
  attachments?: ComposerAttachments;
  placeholder?: string;
  /** Bump to focus the textarea (e.g. after an external prefill). */
  focusToken?: number;
  /** Disable text entry (e.g. while an agent turn is in flight). The
   *  send/stop button is unaffected so a Stop control stays clickable. */
  disabled?: boolean;
}

const DEFAULT_PLACEHOLDER =
  "Type a message or /command… (Shift/Option+Enter for newline, ↑/↓ for history)";

/**
 * The single shared message composer used by both topics and chats: textarea
 * with slash-command autocomplete, ↑/↓ history recall, dictation, optional
 * attachments, a resize handle, and the send/stop button.
 */
export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  streaming,
  suggestions,
  userHistory,
  speech,
  interimText,
  height,
  onResizeStart,
  attachments,
  placeholder,
  focusToken,
  disabled = false,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const historyIndexRef = useRef<number | null>(null);
  const originalDraftRef = useRef<string>("");
  const [pickerIndex, setPickerIndex] = useState(0);
  const [isDraggingFile, setIsDraggingFile] = useState(false);

  const pickerOpen = suggestions.length > 0;
  useEffect(() => setPickerIndex(0), [value]);

  // Focus + move the caret to the end whenever an external prefill bumps the
  // token (e.g. the "Continue" button on an agent summary).
  useEffect(() => {
    if (!focusToken) return;
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    const end = el.value.length;
    el.setSelectionRange(end, end);
  }, [focusToken]);

  const hasPending = (attachments?.pending.length ?? 0) > 0;
  const sendDisabled = disabled || (!value.trim() && !hasPending);

  function triggerSend(): void {
    if (disabled) return;
    historyIndexRef.current = null;
    onSend();
  }

  function selectCommand(cmd: SlashCommand): void {
    onChange(`/${cmd.name} `);
    textareaRef.current?.focus();
  }

  return (
    <>
      {attachments &&
        (attachments.pending.length > 0 ||
          attachments.uploadingCount > 0 ||
          attachments.error) && (
          <div className="flex flex-wrap items-center gap-2">
            {attachments.pending.map((a) => (
              <AttachmentChip
                key={a.id}
                attachment={a}
                onRemove={() => attachments.onRemove(a.id)}
              />
            ))}
            {attachments.uploadingCount > 0 && (
              <span className="text-[11px] text-muted italic px-2 py-1">
                Uploading {attachments.uploadingCount}…
              </span>
            )}
            {attachments.error && (
              <span className="text-[11px] text-red-500 px-2 py-1">{attachments.error}</span>
            )}
          </div>
        )}
      {speech.listening && (
        <div className="flex items-start gap-2 text-[11px] text-muted px-1">
          <span className="inline-block h-2 w-2 mt-1 shrink-0 rounded-full bg-red-500 animate-pulse" />
          <span className="min-w-0 break-words max-h-20 overflow-y-auto">
            Listening… {interimText && <span className="italic">{interimText}</span>}
          </span>
        </div>
      )}
      {speech.error && (
        <div className="text-[11px] text-red-500 px-1">Dictation error: {speech.error}</div>
      )}
      <div
        className={`relative flex items-end gap-2 ${
          isDraggingFile ? "ring-2 ring-accent/60 rounded-md" : ""
        }`}
        onDragOver={(e) => {
          if (attachments && e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            setIsDraggingFile(true);
          }
        }}
        onDragLeave={(e) => {
          if (attachments && !e.currentTarget.contains(e.relatedTarget as Node | null)) {
            setIsDraggingFile(false);
          }
        }}
        onDrop={(e) => {
          if (attachments && e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            setIsDraggingFile(false);
            attachments.onFiles(e.dataTransfer.files);
          }
        }}
      >
        <div
          role="separator"
          aria-orientation="horizontal"
          onMouseDown={onResizeStart}
          title="Drag to resize"
          className="absolute -top-2 left-0 right-0 h-2 cursor-row-resize group z-10"
        >
          <div className="h-px w-12 mx-auto mt-1 bg-border group-hover:bg-accent/60 transition-colors" />
        </div>
        {pickerOpen && (
          <SlashCommandPicker
            commands={suggestions}
            activeIndex={pickerIndex}
            onSelect={selectCommand}
            onHover={setPickerIndex}
          />
        )}
        {attachments && (
          <input
            ref={fileInputRef}
            type="file"
            accept={ATTACHMENT_ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) attachments.onFiles(e.target.files);
              e.target.value = "";
            }}
          />
        )}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => {
            historyIndexRef.current = null;
            onChange(e.target.value);
          }}
          onPaste={(e) => {
            if (!attachments) return;
            const items = e.clipboardData?.items;
            if (!items) return;
            const files: File[] = [];
            for (const it of items) {
              if (it.kind === "file") {
                const f = it.getAsFile();
                if (f) files.push(f);
              }
            }
            if (files.length > 0) {
              e.preventDefault();
              attachments.onFiles(files);
            }
          }}
          onKeyDown={(e) => {
            if (pickerOpen) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setPickerIndex((i) => (i + 1) % suggestions.length);
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setPickerIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
                return;
              }
              if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && !e.altKey)) {
                e.preventDefault();
                selectCommand(suggestions[pickerIndex]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                onChange("");
                return;
              }
            }
            if (e.key === "ArrowUp" && userHistory.length > 0) {
              const ta = e.currentTarget;
              const caretAtTop =
                historyIndexRef.current !== null ||
                !ta.value.slice(0, ta.selectionStart).includes("\n");
              if (caretAtTop) {
                e.preventDefault();
                if (historyIndexRef.current === null) {
                  originalDraftRef.current = value;
                  historyIndexRef.current = userHistory.length - 1;
                } else if (historyIndexRef.current > 0) {
                  historyIndexRef.current -= 1;
                }
                onChange(userHistory[historyIndexRef.current] ?? "");
                return;
              }
            }
            if (e.key === "ArrowDown" && historyIndexRef.current !== null) {
              const ta = e.currentTarget;
              const caretAtBottom = !ta.value.slice(ta.selectionStart).includes("\n");
              if (caretAtBottom) {
                e.preventDefault();
                const next = historyIndexRef.current + 1;
                if (next >= userHistory.length) {
                  historyIndexRef.current = null;
                  onChange(originalDraftRef.current);
                } else {
                  historyIndexRef.current = next;
                  onChange(userHistory[next]);
                }
                return;
              }
            }
            if (e.key === "Enter") {
              if (e.altKey) {
                // Option/Alt+Enter inserts a newline at the caret.
                e.preventDefault();
                const ta = e.currentTarget;
                const start = ta.selectionStart;
                const end = ta.selectionEnd;
                const next = ta.value.slice(0, start) + "\n" + ta.value.slice(end);
                onChange(next);
                requestAnimationFrame(() => {
                  ta.selectionStart = ta.selectionEnd = start + 1;
                });
                historyIndexRef.current = null;
                return;
              }
              if (!e.shiftKey) {
                e.preventDefault();
                triggerSend();
              }
              // Shift+Enter falls through to a default textarea newline.
            }
          }}
          placeholder={placeholder ?? DEFAULT_PLACEHOLDER}
          style={{ height }}
          disabled={disabled}
          className="flex-1 resize-none bg-surface border border-border rounded p-2 text-sm outline-none focus:border-accent disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <div className={`flex gap-2 ${height >= 96 ? "flex-col" : "flex-row items-end"}`}>
          {attachments && (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="px-2 py-2 rounded bg-surface border border-border text-muted hover:text-text hover:bg-bg"
              aria-label="Attach file"
              data-tooltip="Attach file (image/pdf/docx/pptx)"
            >
              <Paperclip size={18} />
            </button>
          )}
          {speech.supported && (
            <button
              type="button"
              onClick={speech.toggle}
              className={`px-2 py-2 rounded border ${
                speech.listening
                  ? "bg-red-600 border-red-600 text-white animate-pulse"
                  : "bg-surface border-border text-muted hover:text-text hover:bg-bg"
              }`}
              aria-label={speech.listening ? "Stop dictation" : "Dictate"}
              aria-pressed={speech.listening}
              data-tooltip={speech.listening ? "Stop dictation" : "Dictate (Azure Speech)"}
            >
              <Mic size={18} />
            </button>
          )}
          {streaming ? (
            <button
              onClick={onStop}
              className="px-3 py-2 rounded bg-surface border border-border hover:bg-bg"
              aria-label="Stop generation"
              data-tooltip="Stop generation"
            >
              <StopCircle size={18} />
            </button>
          ) : (
            <button
              onClick={triggerSend}
              disabled={sendDisabled}
              className="px-3 py-2 rounded bg-accent text-white disabled:opacity-40"
              aria-label="Send"
              data-tooltip="Send (Enter)"
            >
              <Send size={18} />
            </button>
          )}
        </div>
      </div>
    </>
  );
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}) {
  const label = attachment.original_filename || `attachment-${attachment.id}`;
  const isImage = attachment.mime.startsWith("image/");
  return (
    <div className="flex items-center gap-2 pl-1 pr-2 py-1 rounded border border-border bg-surface text-xs max-w-[14rem]">
      {isImage ? (
        <img
          src={api.attachmentUrl(attachment.id)}
          alt=""
          className="w-8 h-8 rounded object-cover border border-border shrink-0"
        />
      ) : (
        <a
          href={api.attachmentUrl(attachment.id)}
          target="_blank"
          rel="noreferrer"
          className="w-8 h-8 rounded border border-border shrink-0 flex items-center justify-center text-muted hover:text-text"
          title={label}
        >
          <Paperclip size={14} />
        </a>
      )}
      <span className="truncate" title={label}>
        {label}
      </span>
      <button
        type="button"
        onClick={onRemove}
        className="p-0.5 rounded text-muted hover:text-text hover:bg-bg shrink-0"
        aria-label={`Remove ${label}`}
        data-tooltip="Remove"
      >
        <X size={12} />
      </button>
    </div>
  );
}
