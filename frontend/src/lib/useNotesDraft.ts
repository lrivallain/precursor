import { useEffect, useMemo, useState } from "react";
import { subscribeNoteDraftChanges } from "./detachedDraftStore";
import {
  splitSupportedAttachmentFiles,
  unsupportedAttachmentMessage,
} from "./attachments";
import type {
  Message,
  NoteDraftAttachment,
  NotesDraft,
  ReminderContainer,
} from "./types";
import type { NotesAction } from "../components/NotesPanel";

export interface PendingNotes {
  initialText: string;
  attachments: NoteDraftAttachment[];
  uploadingAttachments: number;
  attachmentsError: string | null;
  loadingDraft: boolean;
  savingDraft: boolean;
  rephrasing: boolean;
  acting: boolean;
  error: string | null;
  rephrasedText?: string;
}

export interface NotesConfirmState {
  message: string;
  resolve: (ok: boolean) => void;
}

export interface SavedNotesDraft {
  text: string;
  attachmentCount: number;
}

/** Container-agnostic notes API surface (topic vs chat differ only by method). */
export interface NotesApi {
  getDraft: () => Promise<NotesDraft>;
  saveDraft: (text: string) => Promise<NotesDraft>;
  clearDraft: () => Promise<void>;
  append: (text: string, attachmentIds: number[]) => Promise<{ message: Message }>;
  rephrase: (text: string) => Promise<{ text: string }>;
  uploadAttachment: (file: File) => Promise<NoteDraftAttachment>;
  deleteAttachment: (attachmentId: number) => Promise<void>;
}

export interface UseNotesDraftOptions {
  container: ReminderContainer;
  id: number;
  notesApi: NotesApi;
  /** Append persisted rows to the transcript and refresh the sidebar. */
  appendMessages: (messages: Message[]) => void;
  /** Kick off a streamed "append & ask" turn. */
  startAppendAndAsk: (body: string, attachmentIds: number[]) => void;
  /**
   * Topic-only: post the notes as a GitHub issue comment and return the rows to
   * append. Omit for surfaces (chats) that don't offer post-comment.
   */
  onPostComment?: (text: string, attachmentIds: number[]) => Promise<Message[]>;
  /** Append a local-only system note (surface-specific). */
  systemNote: (content: string) => void;
}

export interface NotesDraftController {
  pendingNotes: PendingNotes | null;
  savedNotesDraft: SavedNotesDraft | null;
  notesConfirm: NotesConfirmState | null;
  /** Settle and dismiss the pending in-pad confirm prompt. */
  resolveNotesConfirm: (ok: boolean) => void;
  /** Open the notes pad and load any saved draft (the `/notes` command). */
  openNotesPad: () => Promise<void>;
  resumeSavedNotesDraft: () => Promise<void>;
  discardSavedNotesDraft: () => Promise<void>;
  uploadNoteAttachments: (files: Iterable<File>) => Promise<void>;
  removeNoteAttachment: (id: number) => Promise<void>;
  rephraseNotes: (text: string) => Promise<void>;
  saveNotesDraft: (text: string) => Promise<void>;
  runNotesAction: (action: NotesAction, text: string) => Promise<void>;
  closeNotesPad: (text: string) => Promise<void>;
  /** Close the pad immediately without a confirm prompt (e.g. on pop-out). */
  dismissPad: () => void;
}

const EMPTY_PAD: PendingNotes = {
  initialText: "",
  attachments: [],
  uploadingAttachments: 0,
  attachmentsError: null,
  loadingDraft: true,
  savingDraft: false,
  rephrasing: false,
  acting: false,
  error: null,
};

function toSavedDraft(res: NotesDraft | null): SavedNotesDraft | null {
  const text = (res?.text ?? "").trim();
  return text || (res?.attachments.length ?? 0)
    ? { text, attachmentCount: res?.attachments.length ?? 0 }
    : null;
}

/**
 * Notes-pad state and handlers shared by the conversation panels: draft
 * loading, the saved-draft banner, attachment upload/removal, rephrase, and the
 * append / append-and-ask / post-comment actions. The topic and chat panels
 * previously duplicated all of this; the only real delta is the API surface
 * (injected via `notesApi`) and the topic-only post-comment action.
 */
export function useNotesDraft({
  container,
  id,
  notesApi,
  appendMessages,
  startAppendAndAsk,
  onPostComment,
  systemNote,
}: UseNotesDraftOptions): NotesDraftController {
  const [pendingNotes, setPendingNotes] = useState<PendingNotes | null>(null);
  const [savedNotesDraft, setSavedNotesDraft] = useState<SavedNotesDraft | null>(null);
  const [notesConfirm, setNotesConfirm] = useState<NotesConfirmState | null>(null);

  const reloadSavedNotesDraft = useMemo(
    () => async () => {
      const res = await notesApi.getDraft().catch(() => null);
      setSavedNotesDraft(toSavedDraft(res));
    },
    [notesApi],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const res = await notesApi.getDraft().catch(() => null);
      if (cancelled) return;
      setSavedNotesDraft(toSavedDraft(res));
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Keep the saved-draft banner live when a popped-out notes window persists a
  // draft for this conversation (otherwise it stays stale until a refresh).
  useEffect(() => {
    return subscribeNoteDraftChanges((changed, changedId) => {
      if (changed === container && changedId === id) {
        void reloadSavedNotesDraft();
      }
    });
  }, [container, id, reloadSavedNotesDraft]);

  // Open the pad and hydrate it from the saved draft. `syncSaved` also refreshes
  // the saved-draft banner (used by the `/notes` command entry point).
  async function beginPad(syncSaved: boolean): Promise<void> {
    setPendingNotes(EMPTY_PAD);
    try {
      const draftRes = await notesApi.getDraft();
      if (syncSaved) setSavedNotesDraft(toSavedDraft(draftRes));
      setPendingNotes((p) =>
        p
          ? {
              ...p,
              initialText: draftRes.text ?? "",
              attachments: draftRes.attachments,
              loadingDraft: false,
            }
          : p,
      );
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, loadingDraft: false, error: (err as Error).message } : p,
      );
    }
  }

  const openNotesPad = () => beginPad(true);
  const resumeSavedNotesDraft = () => beginPad(false);

  function resolveNotesConfirm(ok: boolean): void {
    notesConfirm?.resolve(ok);
    setNotesConfirm(null);
  }

  async function askNotesConfirm(message: string): Promise<boolean> {
    return await new Promise<boolean>((resolve) => {
      setNotesConfirm({ message, resolve });
    });
  }

  async function uploadNoteAttachments(files: Iterable<File>): Promise<void> {
    if (!pendingNotes) return;
    const { supported, unsupported } = splitSupportedAttachmentFiles(files);
    if (supported.length === 0) {
      if (unsupported.length > 0) {
        setPendingNotes((p) =>
          p ? { ...p, attachmentsError: unsupportedAttachmentMessage(unsupported) } : p,
        );
      }
      return;
    }
    setPendingNotes((p) =>
      p
        ? {
            ...p,
            attachmentsError:
              unsupported.length > 0 ? unsupportedAttachmentMessage(unsupported) : null,
            uploadingAttachments: p.uploadingAttachments + supported.length,
          }
        : p,
    );
    try {
      for (const file of supported) {
        try {
          const att = await notesApi.uploadAttachment(file);
          setPendingNotes((p) => (p ? { ...p, attachments: [...p.attachments, att] } : p));
        } catch (err) {
          setPendingNotes((p) =>
            p ? { ...p, attachmentsError: (err as Error).message || "Upload failed" } : p,
          );
        }
      }
    } finally {
      setPendingNotes((p) =>
        p
          ? {
              ...p,
              uploadingAttachments: Math.max(0, p.uploadingAttachments - supported.length),
            }
          : p,
      );
    }
  }

  async function removeNoteAttachment(attachmentId: number): Promise<void> {
    if (!pendingNotes) return;
    setPendingNotes((p) =>
      p ? { ...p, attachments: p.attachments.filter((a) => a.id !== attachmentId) } : p,
    );
    try {
      await notesApi.deleteAttachment(attachmentId);
    } catch {
      // ignore stale/deleted ids
    }
  }

  async function rephraseNotes(text: string): Promise<void> {
    if (!pendingNotes || !text.trim()) return;
    setPendingNotes((p) => (p ? { ...p, rephrasing: true, error: null } : p));
    try {
      const res = await notesApi.rephrase(text);
      setPendingNotes((p) => (p ? { ...p, rephrasing: false, rephrasedText: res.text } : p));
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, rephrasing: false, error: (err as Error).message } : p,
      );
    }
  }

  async function runNotesAction(action: NotesAction, text: string): Promise<void> {
    if (!pendingNotes) return;
    const trimmed = text.trim();
    const attachmentIds = pendingNotes.attachments.map((a) => a.id);
    if (!trimmed && attachmentIds.length === 0) return;
    setPendingNotes((p) => (p ? { ...p, acting: true, error: null } : p));
    try {
      if (action === "append") {
        const res = await notesApi.append(trimmed, attachmentIds);
        await notesApi.clearDraft().catch(() => {});
        setSavedNotesDraft(null);
        appendMessages([res.message]);
        setPendingNotes(null);
      } else if (action === "append-and-ask") {
        setSavedNotesDraft(null);
        setPendingNotes(null);
        const body = trimmed ? `**Notes**\n\n${trimmed}` : "**Notes**";
        startAppendAndAsk(body, attachmentIds);
      } else if (action === "post-comment" && onPostComment) {
        const rows = await onPostComment(trimmed, attachmentIds);
        await notesApi.clearDraft().catch(() => {});
        setSavedNotesDraft(null);
        appendMessages(rows);
        setPendingNotes(null);
      }
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, acting: false, error: (err as Error).message } : p,
      );
    }
  }

  async function saveNotesDraft(text: string): Promise<void> {
    if (!pendingNotes) return;
    if (!text.trim() && pendingNotes.attachments.length === 0) return;
    if (!(await askNotesConfirm("Save notes as draft and close the pad?"))) return;
    setPendingNotes((p) => (p ? { ...p, savingDraft: true, error: null } : p));
    try {
      const res = await notesApi.saveDraft(text.trim());
      setSavedNotesDraft(toSavedDraft(res));
      setPendingNotes(null);
    } catch (err) {
      setPendingNotes((p) =>
        p ? { ...p, savingDraft: false, error: (err as Error).message } : p,
      );
    }
  }

  async function discardSavedNotesDraft(): Promise<void> {
    if (!(await askNotesConfirm("Discard the saved notes draft?"))) return;
    try {
      await notesApi.clearDraft();
      setSavedNotesDraft(null);
    } catch (err) {
      systemNote(`Draft discard failed: ${(err as Error).message}`);
    }
  }

  async function closeNotesPad(text: string): Promise<void> {
    const dirty = !!pendingNotes && (!!text.trim() || pendingNotes.attachments.length > 0);
    if (dirty && !(await askNotesConfirm("Discard current notes in the pad?"))) return;
    if (dirty) {
      await notesApi.clearDraft().catch(() => {});
      setSavedNotesDraft(null);
    }
    setPendingNotes(null);
  }

  return {
    pendingNotes,
    savedNotesDraft,
    notesConfirm,
    resolveNotesConfirm,
    openNotesPad,
    resumeSavedNotesDraft,
    discardSavedNotesDraft,
    uploadNoteAttachments,
    removeNoteAttachment,
    rephraseNotes,
    saveNotesDraft,
    runNotesAction,
    closeNotesPad,
    dismissPad: () => setPendingNotes(null),
  };
}
