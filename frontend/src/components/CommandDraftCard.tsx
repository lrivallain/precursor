import { useEffect, useState } from "react";
import { Eye, Loader2, Pencil, Send, X } from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { Markdown } from "./Markdown";

export interface CommandDraftPayload {
  title?: string;
  body: string;
}

interface Props {
  title: string;
  subtitle?: string;
  initialBody: string;
  /** When provided, render a title input above the body. */
  initialTitle?: string;
  titleLabel?: string;
  bodyPlaceholder?: string;
  /** When false, the post button is enabled even with an empty body. */
  bodyRequired?: boolean;
  loading?: boolean; // true while the draft is being generated
  posting?: boolean; // true while the post is in-flight
  error?: string | null;
  sendLabel?: string;
  postingLabel?: string;
  /** Optional warning shown above the action buttons. */
  confirmHint?: string;
  onSend: (payload: CommandDraftPayload) => void | Promise<void>;
  onCancel: () => void;
}

type Mode = "edit" | "preview";

export function CommandDraftCard({
  title,
  subtitle,
  initialBody,
  initialTitle,
  titleLabel = "Title",
  bodyPlaceholder = "Write in GitHub-Flavored Markdown…",
  bodyRequired = true,
  loading = false,
  posting = false,
  error,
  sendLabel = "Post comment",
  postingLabel = "Posting…",
  confirmHint,
  onSend,
  onCancel,
}: Props) {
  const hasTitleField = initialTitle !== undefined;
  const [titleValue, setTitleValue] = useState(initialTitle ?? "");
  const [body, setBody] = useState(initialBody);
  const [mode, setMode] = useState<Mode>("edit");

  // When the parent finishes generating the draft, seed the inputs once.
  useEffect(() => {
    if (initialBody && body === "") setBody(initialBody);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialBody]);
  useEffect(() => {
    if (initialTitle && titleValue === "") setTitleValue(initialTitle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTitle]);

  const titleOk = !hasTitleField || titleValue.trim().length > 0;
  const bodyOk = bodyRequired ? body.trim().length > 0 : true;
  const canSend = titleOk && bodyOk && !loading && !posting;

  return (
    <div className="border border-border rounded-lg bg-surface shadow-sm">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <Github size={14} className="text-muted" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{title}</div>
          {subtitle && (
            <div className="text-[11px] text-muted truncate">{subtitle}</div>
          )}
        </div>
        <div className="flex items-center gap-1 text-xs">
          <button
            type="button"
            onClick={() => setMode("edit")}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded ${
              mode === "edit" ? "bg-bg text-text" : "text-muted hover:text-text"
            }`}
            aria-pressed={mode === "edit"}
          >
            <Pencil size={11} /> Edit
          </button>
          <button
            type="button"
            onClick={() => setMode("preview")}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded ${
              mode === "preview" ? "bg-bg text-text" : "text-muted hover:text-text"
            }`}
            aria-pressed={mode === "preview"}
          >
            <Eye size={11} /> Preview
          </button>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded text-muted hover:bg-bg"
          aria-label="Cancel draft"
          data-tooltip="Cancel draft"
        >
          <X size={14} />
        </button>
      </div>

      <div className="p-3 space-y-2">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" />
            Drafting from recent discussion…
          </div>
        ) : mode === "edit" ? (
          <>
            {hasTitleField && (
              <label className="block">
                <span className="block text-[11px] text-muted mb-1">
                  {titleLabel}
                </span>
                <input
                  type="text"
                  value={titleValue}
                  onChange={(e) => setTitleValue(e.target.value)}
                  className="w-full bg-bg border border-border rounded p-2 text-sm outline-none focus:border-accent"
                />
              </label>
            )}
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={8}
              className="w-full resize-y bg-bg border border-border rounded p-2 text-sm font-mono leading-relaxed outline-none focus:border-accent"
              placeholder={bodyPlaceholder}
            />
          </>
        ) : (
          <div className="text-sm leading-relaxed bg-bg border border-border rounded p-3 min-h-[8rem]">
            {hasTitleField && titleValue.trim() && (
              <h3 className="!mt-0 !mb-2 text-base font-semibold">
                {titleValue.trim()}
              </h3>
            )}
            {body.trim() ? (
              <Markdown>{body}</Markdown>
            ) : (
              <span className="text-muted italic">
                {bodyRequired
                  ? "Nothing to preview."
                  : "(empty — will be sent without a comment)"}
              </span>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="px-3 pb-2 text-xs text-red-500 break-words">{error}</div>
      )}

      <div className="flex items-center justify-end gap-2 px-3 py-2 border-t border-border">
        {confirmHint && (
          <span className="text-[11px] text-muted mr-auto">{confirmHint}</span>
        )}
        <button
          type="button"
          onClick={onCancel}
          disabled={posting}
          className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() =>
            void onSend({
              body,
              ...(hasTitleField ? { title: titleValue.trim() } : {}),
            })
          }
          disabled={!canSend}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs bg-accent text-white disabled:opacity-50"
        >
          {posting ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
          {posting ? postingLabel : sendLabel}
        </button>
      </div>
    </div>
  );
}
