import { useEffect, useState } from "react";
import { Loader2, Send } from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { CommandPanel } from "./CommandPanel";

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
    <CommandPanel
      icon={<Github size={14} />}
      title={title}
      subtitle={subtitle}
      onClose={onCancel}
      closeLabel="Cancel draft"
      loading={loading}
      titleField={
        hasTitleField
          ? { value: titleValue, onChange: setTitleValue, label: titleLabel }
          : undefined
      }
      body={body}
      onBodyChange={setBody}
      bodyPlaceholder={bodyPlaceholder}
      bodyClassName="font-mono leading-relaxed"
      resizeStorageKey="precursor:commandDraft:height"
      previewEmptyHint={
        bodyRequired ? "Nothing to preview." : "(empty — will be sent without a comment)"
      }
      disabled={posting}
      error={error}
      footer={
        <>
          {confirmHint && <span className="text-[11px] text-muted mr-auto">{confirmHint}</span>}
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
        </>
      }
    />
  );
}

