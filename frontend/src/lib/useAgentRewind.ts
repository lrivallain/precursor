import { useCallback, useEffect, useRef, useState } from "react";
import type { RewindMode } from "./timeline";
import type {
  RewindController,
  RewindFileChange,
  RewindFiles,
  RewindNotice,
  RewindPreview,
} from "./useRewind";
import type { AgentRewindMode, AgentRewindPreview, AgentRewindResult, TurnIndexItem } from "./types";

/**
 * A cockpit exchange (a human prompt, its work and its answer) as a timeline
 * turn. `message_id` holds the exchange index, which is what the prompt's
 * `TURN_ANCHOR_ATTR` carries.
 */
export interface AgentTurn extends TurnIndexItem {
  /** SDK id of the prompt when this turn can be rewound, else null. */
  event_id: string | null;
  /** The whole prompt, handed back to the composer by Edit & resend. */
  text: string;
  /** The run that sent the prompt, for grouping the rail by run. */
  agent_run_id?: number | null;
}

export interface UseAgentRewindOptions {
  /** Identity of the transcript on screen; a change drops a preview. */
  resetKey: string;
  turns: readonly AgentTurn[];
  /** A turn is in flight: the server refuses a rewind meanwhile. */
  streaming: boolean;
  rewind: (eventId: string, mode: AgentRewindMode) => Promise<AgentRewindResult | unknown>;
  /** Lists the files a rewind to `eventId` could restore; omit for conversation only. */
  previewRewind?: (eventId: string) => Promise<AgentRewindPreview>;
  setDraft: (text: string) => void;
  /** Run after a rewind lands so the transcript and sidebar resync. */
  onSettled: () => void;
  onError: (message: string) => void;
}

const UNAVAILABLE: Record<string, string> = {
  "file-change-tracking-disabled":
    "Files stay as they are: this session wasn't tracking file changes from its start.",
  "unsupported-remote-session": "Files stay as they are: a remote session can't restore them.",
  "session-busy": "Files stay as they are: the session is still settling.",
};

interface FilesState {
  loading: boolean;
  unavailable: string | null;
  files: RewindFileChange[];
}

const NO_FILES: FilesState = { loading: false, unavailable: null, files: [] };

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function noticeFor(result: AgentRewindResult): RewindNotice | null {
  if (result.outcome === "unconfirmed") {
    return { text: "Rewound, but the session didn't report which files it restored. Check the agent's files." };
  }
  const restored = result.restored_files ?? [];
  const skipped = result.skipped_files ?? [];
  if (restored.length === 0 && skipped.length === 0) return null;
  let text = `Restored ${plural(restored.length, "file")}`;
  if (skipped.length > 0) text += ` · ${skipped.length} left as they were`;
  const detail = skipped
    .map(
      (f) =>
        `${f.path}: ${f.reason === "user-modified" ? "changed since the agent edited it" : "no restorable copy"}`,
    )
    .join("\n");
  return { text, detail: detail || undefined };
}

/**
 * Rewind for an agent session: preview → explicit confirmation → done. Unlike
 * topics and chats there is no undo grace, because the SDK can't reverse a
 * rewind; the confirmation says so instead. When the session tracked its file
 * changes, the preview also offers to put back the files the dropped turns
 * changed (opt-in for every rewind).
 */
export function useAgentRewind({
  resetKey,
  turns,
  streaming,
  rewind,
  previewRewind,
  setDraft,
  onSettled,
  onError,
}: UseAgentRewindOptions): RewindController {
  const [preview, setPreview] = useState<RewindPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<FilesState>(NO_FILES);
  const [restore, setRestore] = useState(false);
  const [notice, setNotice] = useState<RewindNotice | null>(null);
  // Discards a file lookup answering a cut the user has since moved off.
  const lookup = useRef(0);

  const clearPreview = useCallback(() => {
    lookup.current += 1;
    setPreview(null);
    setFiles(NO_FILES);
    setRestore(false);
  }, []);

  useEffect(() => {
    clearPreview();
    setNotice(null);
  }, [resetKey, clearPreview]);
  useEffect(() => {
    if (streaming) clearPreview();
  }, [streaming, clearPreview]);

  // Only the first dropped prompt has to be rewindable: the SDK drops what
  // follows it, and only the current session's prompts carry an id.
  const canRewind = useCallback(
    (index: number, mode: RewindMode) => {
      const first = mode === "edit" ? index : index + 1;
      return index >= 0 && first < turns.length && turns[first].event_id != null;
    },
    [turns],
  );

  async function lookUpFiles(eventId: string): Promise<void> {
    if (!previewRewind) return;
    const token = ++lookup.current;
    setFiles({ loading: true, unavailable: null, files: [] });
    try {
      const found = await previewRewind(eventId);
      if (token !== lookup.current) return;
      const unavailable = !found.files_available
        ? (UNAVAILABLE[found.unavailable_reason ?? ""] ?? "Files stay as they are.")
        : found.files.length === 0
          ? "The dropped turns changed no tracked files."
          : null;
      setFiles({ loading: false, unavailable, files: found.files });
    } catch (err) {
      if (token !== lookup.current) return;
      setFiles({ loading: false, unavailable: (err as Error).message, files: [] });
    }
  }

  function start(_turns: readonly TurnIndexItem[], index: number, mode: RewindMode): void {
    if (streaming || busy || !canRewind(index, mode)) return;
    const first = mode === "edit" ? index : index + 1;
    setNotice(null);
    setRestore(false);
    setPreview({
      index,
      mode,
      fromId: turns[first].message_id,
      droppedTurns: turns.length - first,
    });
    void lookUpFiles(turns[first].event_id as string);
  }

  async function apply(cut: RewindPreview): Promise<void> {
    const turn = turns.find((t) => t.message_id === cut.fromId);
    if (!turn?.event_id) {
      clearPreview();
      return;
    }
    const withFiles = restore && files.unavailable == null && files.files.length > 0;
    setBusy(true);
    try {
      const result = await rewind(turn.event_id, withFiles ? "conversation-and-files" : "conversation");
      if (cut.mode === "edit") setDraft(turn.text);
      if (withFiles && result && typeof result === "object") {
        setNotice(noticeFor(result as AgentRewindResult));
      }
      onSettled();
    } catch (err) {
      onError(`Rewind failed: ${(err as Error).message}`);
    } finally {
      clearPreview();
      setBusy(false);
    }
  }

  const filesSlot: RewindFiles | null =
    preview && previewRewind ? { ...files, restore, setRestore: (on) => setRestore(on && !busy) } : null;

  return {
    preview,
    pending: null,
    hidden: null,
    start,
    cancel: () => {
      if (!busy) clearPreview();
    },
    confirm: () => {
      if (preview && !streaming && !busy && !files.loading) void apply(preview);
    },
    undo: () => {},
    flush: () => Promise.resolve(true),
    canRewind,
    irreversible: true,
    busy,
    files: filesSlot,
    notice,
    dismissNotice: () => setNotice(null),
  };
}
