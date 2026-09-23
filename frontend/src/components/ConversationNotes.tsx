import { NotesPanel } from "./NotesPanel";
import { Modal } from "./Modal";
import { detachedDraftStore } from "../lib/detachedDraftStore";
import { Z_INDEX } from "../lib/constants";
import type { ConvKind } from "../lib/streamStore";
import type { NotesDraftController } from "../lib/useNotesDraft";

interface ConversationNotesProps {
  notes: NotesDraftController;
  container: ConvKind;
  containerId: number;
  /** Conversation title, used to name the popped-out notes window. */
  title: string;
  hasIssue: boolean;
  /** Offer "post as issue comment" (topics only). */
  allowPostComment: boolean;
}

/** The composer-side notes pad: a "saved draft" strip, or the open pad itself. */
export function ConversationNotes({
  notes,
  container,
  containerId,
  title,
  hasIssue,
  allowPostComment,
}: ConversationNotesProps) {
  const { pendingNotes, savedNotesDraft } = notes;
  return (
    <>
      {!pendingNotes && savedNotesDraft && (
        <div className="flex items-center justify-between gap-2 rounded border border-border bg-surface px-3 py-1.5 text-xs">
          <span className="min-w-0 flex-1 truncate text-muted">
            Saved notes draft:
            {savedNotesDraft.text ? ` ${savedNotesDraft.text}` : ""}
            {savedNotesDraft.attachmentCount > 0
              ? ` (${savedNotesDraft.attachmentCount} attachment${
                  savedNotesDraft.attachmentCount > 1 ? "s" : ""
                })`
              : ""}
          </span>
          <div className="flex items-center gap-1">
            <button
              className="shrink-0 rounded px-2 py-0.5 text-accent hover:bg-border"
              onClick={() => void notes.resumeSavedNotesDraft()}
            >
              Resume
            </button>
            <button
              className="shrink-0 rounded px-2 py-0.5 text-muted hover:bg-border"
              onClick={() => void notes.discardSavedNotesDraft()}
            >
              Discard
            </button>
          </div>
        </div>
      )}
      {pendingNotes && (
        <NotesPanel
          hasIssue={hasIssue}
          allowPostComment={allowPostComment}
          initialText={pendingNotes.initialText}
          loadingDraft={pendingNotes.loadingDraft}
          savingDraft={pendingNotes.savingDraft}
          rephrasing={pendingNotes.rephrasing}
          acting={pendingNotes.acting}
          error={pendingNotes.error}
          attachments={pendingNotes.attachments}
          uploadingAttachments={pendingNotes.uploadingAttachments}
          attachmentsError={pendingNotes.attachmentsError}
          rephrasedText={pendingNotes.rephrasedText}
          onRephrase={notes.rephraseNotes}
          onSaveDraft={notes.saveNotesDraft}
          onAction={notes.runNotesAction}
          onAttachFiles={notes.uploadNoteAttachments}
          onRemoveAttachment={notes.removeNoteAttachment}
          onCancel={notes.closeNotesPad}
          onPopOut={
            pendingNotes.loadingDraft
              ? undefined
              : (text) => {
                  detachedDraftStore.open({
                    kind: "notes",
                    container,
                    containerId,
                    title: `Notes — ${title}`,
                    hasIssue,
                    allowPostComment,
                    initialText: text,
                    initialAttachments: pendingNotes.attachments,
                  });
                  notes.dismissPad();
                }
          }
        />
      )}
    </>
  );
}

/** The in-pad confirm prompt raised by a notes action (e.g. discarding a draft). */
export function NotesConfirmModal({ notes }: { notes: NotesDraftController }) {
  const { notesConfirm, resolveNotesConfirm } = notes;
  if (!notesConfirm) return null;
  return (
    <Modal
      zIndex={Z_INDEX.MODAL_NESTED}
      padded
      closeOnBackdrop={false}
      panelClassName="w-full max-w-sm rounded-lg border border-border bg-surface p-4 shadow-2xl"
    >
      <div className="text-sm">{notesConfirm.message}</div>
      <div className="mt-4 flex justify-end gap-2">
        <button
          className="rounded border border-border px-3 py-1.5 text-xs hover:bg-bg"
          onClick={() => resolveNotesConfirm(false)}
        >
          Cancel
        </button>
        <button
          className="rounded bg-accent px-3 py-1.5 text-xs text-white"
          onClick={() => resolveNotesConfirm(true)}
        >
          Confirm
        </button>
      </div>
    </Modal>
  );
}
