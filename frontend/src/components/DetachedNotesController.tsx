import { useRef, useState } from "react";
import { NotesPanel, type NotesAction } from "./NotesPanel";
import { DetachedWindowPortal } from "./DetachedWindowPortal";
import { api } from "../lib/api";
import { convKey, streamStore } from "../lib/streamStore";
import {
  splitSupportedAttachmentFiles,
  unsupportedAttachmentMessage,
} from "../lib/attachments";
import type { DetachedSession } from "../lib/detachedDraftStore";
import type { NoteDraftAttachment } from "../lib/types";

interface Props {
  session: DetachedSession;
  /** Remove the session from the store (which closes the window). */
  onDone: () => void;
}

/** Topic vs chat differ only in which API endpoints back the note actions. */
function notesApi(session: DetachedSession) {
  const id = session.containerId;
  if (session.container === "topic") {
    return {
      rephrase: (t: string) => api.rephraseNotes(id, t),
      append: (t: string, ids: number[]) => api.appendNotes(id, t, ids),
      save: (t: string) => api.saveNotesDraft(id, t),
      clear: () => api.clearNotesDraft(id),
      upload: (f: File) => api.uploadNoteAttachment(id, f),
      remove: (aid: number) => api.deleteNoteAttachment(id, aid),
      postComment: (t: string, ids: number[]) => api.postGhUpdate(id, t, ids),
    };
  }
  return {
    rephrase: (t: string) => api.rephraseChatNotes(id, t),
    append: (t: string, ids: number[]) => api.appendChatNotes(id, t, ids),
    save: (t: string) => api.saveChatNotesDraft(id, t),
    clear: () => api.clearChatNotesDraft(id),
    upload: (f: File) => api.uploadChatNoteAttachment(id, f),
    remove: (aid: number) => api.deleteChatNoteAttachment(id, aid),
    postComment: undefined as undefined | ((t: string, ids: number[]) => Promise<unknown>),
  };
}

/**
 * Standalone notes scratch pad that lives in its own browser window and keeps
 * acting on the conversation it was popped out from — even after the user
 * navigates elsewhere in the main app. State lives here (in the persistent
 * app-level host), while the view is rendered into the detached window.
 */
export function DetachedNotesController({ session, onDone }: Props) {
  const backend = notesApi(session);
  const streamKey = convKey(session.container, session.containerId);

  const [attachments, setAttachments] = useState<NoteDraftAttachment[]>(
    session.initialAttachments ?? [],
  );
  const [uploadingAttachments, setUploadingAttachments] = useState(0);
  const [attachmentsError, setAttachmentsError] = useState<string | null>(null);
  const [rephrasing, setRephrasing] = useState(false);
  const [rephrasedText, setRephrasedText] = useState<string | undefined>(undefined);
  const [acting, setActing] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The view owns the live text; mirror it here so a raw window close can still
  // persist the draft.
  const latestText = useRef(session.initialText);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  async function rephrase(text: string): Promise<void> {
    if (!text.trim()) return;
    setRephrasing(true);
    setError(null);
    try {
      const res = await backend.rephrase(text);
      setRephrasedText(res.text);
      setRephrasing(false);
    } catch (err) {
      setRephrasing(false);
      setError((err as Error).message);
    }
  }

  async function uploadFiles(files: Iterable<File>): Promise<void> {
    const { supported, unsupported } = splitSupportedAttachmentFiles(files);
    if (supported.length === 0) {
      if (unsupported.length > 0) setAttachmentsError(unsupportedAttachmentMessage(unsupported));
      return;
    }
    setAttachmentsError(unsupported.length > 0 ? unsupportedAttachmentMessage(unsupported) : null);
    setUploadingAttachments((n) => n + supported.length);
    try {
      for (const file of supported) {
        try {
          const att = await backend.upload(file);
          setAttachments((prev) => [...prev, att]);
        } catch (err) {
          setAttachmentsError((err as Error).message || "Upload failed");
        }
      }
    } finally {
      setUploadingAttachments((n) => Math.max(0, n - supported.length));
    }
  }

  async function removeAttachment(id: number): Promise<void> {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    try {
      await backend.remove(id);
    } catch {
      /* ignore stale ids */
    }
  }

  async function saveDraft(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed && attachmentsRef.current.length === 0) return;
    setSavingDraft(true);
    setError(null);
    try {
      await backend.save(trimmed);
      onDone();
    } catch (err) {
      setSavingDraft(false);
      setError((err as Error).message);
    }
  }

  async function runAction(action: NotesAction, text: string): Promise<void> {
    const trimmed = text.trim();
    const attachmentIds = attachmentsRef.current.map((a) => a.id);
    if (!trimmed && attachmentIds.length === 0) return;
    setActing(true);
    setError(null);
    try {
      if (action === "append") {
        await backend.append(trimmed, attachmentIds);
        await backend.clear().catch(() => {});
        onDone();
      } else if (action === "append-and-ask") {
        const body = trimmed ? `**Notes**\n\n${trimmed}` : "**Notes**";
        void streamStore.start(streamKey, body, undefined, undefined, attachmentIds);
        onDone();
      } else if (action === "post-comment" && backend.postComment) {
        await backend.postComment(trimmed, attachmentIds);
        await backend.clear().catch(() => {});
        onDone();
      }
    } catch (err) {
      setActing(false);
      setError((err as Error).message);
    }
  }

  // The panel's X (close) button — persist whatever is there, then tear down.
  function cancel(text: string): void {
    latestText.current = text;
    void persistAndClose();
  }

  // Raw OS window close — persist the last-known text best-effort.
  async function persistAndClose(): Promise<void> {
    const trimmed = latestText.current.trim();
    if (trimmed || attachmentsRef.current.length > 0) {
      await backend.save(trimmed).catch(() => {});
    }
    onDone();
  }

  return (
    <DetachedWindowPortal
      title={session.title}
      onUserClose={() => void persistAndClose()}
    >
      <NotesPanel
        variant="embedded"
        hasIssue={session.hasIssue ?? false}
        allowPostComment={session.allowPostComment ?? false}
        initialText={session.initialText}
        loadingDraft={false}
        savingDraft={savingDraft}
        rephrasing={rephrasing}
        acting={acting}
        error={error}
        attachments={attachments}
        uploadingAttachments={uploadingAttachments}
        attachmentsError={attachmentsError}
        rephrasedText={rephrasedText}
        onTextChange={(t) => {
          latestText.current = t;
        }}
        onRephrase={rephrase}
        onSaveDraft={saveDraft}
        onAction={runAction}
        onAttachFiles={uploadFiles}
        onRemoveAttachment={removeAttachment}
        onCancel={cancel}
      />
    </DetachedWindowPortal>
  );
}
