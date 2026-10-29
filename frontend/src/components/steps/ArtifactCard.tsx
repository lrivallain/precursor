import { createContext, useContext, useEffect, useRef, useState } from "react";
import { ArrowLeft, CheckCircle2, FileText, Loader2 } from "lucide-react";
import { Markdown } from "../Markdown";

/** What a plan discussion offers for a deliverable the model marked in its reply. */
export interface ArtifactAction {
  label: string;
  tooltip: string;
  /** Resolves once the deliverable of that message was saved (or refused). */
  send: (messageId: number) => Promise<void>;
  /** Called while a finished proposal is on screen, so the divider arrow knows it has something to send. */
  register?: (messageId: number, artifact: string) => () => void;
  /** Where this proposal's text already lives (a version of the artifact, the final output), if anywhere. */
  applied?: (artifact: string) => AppliedInfo | null;
}

export interface AppliedInfo {
  /** "Applied as v3": where the text went. */
  label: string;
  /** It is what the artifact holds now (not an older version). */
  current: boolean;
}

export const ArtifactActionContext = createContext<ArtifactAction | null>(null);

const BLOCKS = [
  /<!--\s*artifact\s*-->([\s\S]*?)<!--\s*\/artifact\s*-->/i,
  /<artifact>([\s\S]*?)<\/artifact>/i,
];
const OPEN = [/<!--\s*artifact\s*-->/i, /<artifact>/i];

export interface SplitReply {
  /** The reply without the deliverable. */
  text: string;
  artifact: string;
  /** False while the closing marker hasn't streamed in yet. */
  complete: boolean;
}

/** Separate the marked deliverable of a model reply from its discussion, or null when it has none. */
export function splitArtifactReply(content: string): SplitReply | null {
  for (const re of BLOCKS) {
    const match = re.exec(content);
    if (match) {
      const text = (content.slice(0, match.index) + content.slice(match.index + match[0].length)).trim();
      return { text, artifact: match[1].trim(), complete: true };
    }
  }
  for (const re of OPEN) {
    const match = re.exec(content);
    if (match) {
      return {
        text: content.slice(0, match.index).trim(),
        artifact: content.slice(match.index + match[0].length).trim(),
        complete: false,
      };
    }
  }
  return null;
}

/** A deliverable the model wrote in a plan discussion, laid out as a card with its own "send" action. */
export function ArtifactCard({ messageId, artifact, complete }: { messageId: number; artifact: string; complete: boolean }) {
  const action = useContext(ArtifactActionContext);
  const [sending, setSending] = useState(false);
  const applied = action?.applied?.(artifact) ?? null;
  const register = action?.register;
  const registerRef = useRef(register);
  registerRef.current = register;

  useEffect(() => {
    if (!complete) return;
    return registerRef.current?.(messageId, artifact);
  }, [complete, messageId, artifact]);

  async function send(): Promise<void> {
    if (!action || sending) return;
    setSending(true);
    try {
      await action.send(messageId);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mt-2 w-full overflow-hidden rounded-xl border border-accent/40 bg-accent/[0.04]" data-testid="artifact-card">
      <div className="flex items-center gap-2 border-b border-accent/20 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent">
        <FileText size={14} aria-hidden />
        <span className="flex-1">{complete ? "Proposed artifact" : "Writing the artifact…"}</span>
        {!complete && <Loader2 size={13} className="animate-spin" aria-hidden />}
        {applied && (
          <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300" data-testid="artifact-card-applied">
            <CheckCircle2 size={13} /> {applied.current ? applied.label : `${applied.label} earlier`}
          </span>
        )}
      </div>
      <div className="max-h-96 overflow-y-auto px-3 py-2">
        <Markdown className="text-sm leading-relaxed">{artifact || "…"}</Markdown>
      </div>
      {complete && action && (
        <div className="flex justify-end border-t border-accent/20 px-3 py-2">
          <button
            type="button"
            onClick={() => void send()}
            disabled={sending || applied?.current === true}
            data-tooltip={applied?.current ? "Already in the artifact: nothing left to send." : action.tooltip}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-60"
          >
            {sending ? (
              <Loader2 size={14} className="animate-spin" />
            ) : applied?.current ? (
              <CheckCircle2 size={14} />
            ) : (
              <ArrowLeft size={14} strokeWidth={2.5} />
            )}
            {applied?.current ? applied.label : action.label}
          </button>
        </div>
      )}
    </div>
  );
}
