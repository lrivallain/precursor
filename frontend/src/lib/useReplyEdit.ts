import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import type { Message } from "./types";

/** A source range to pre-select when the editor opens (from a selection). */
export interface ReplyEditSelection {
  start: number;
  end: number;
}

export interface ReplyEditController {
  /** The reply being edited in place, if any. */
  editingId: number | null;
  selection: ReplyEditSelection | null;
  saving: boolean;
  error: string | null;
  start: (messageId: number, selection?: ReplyEditSelection | null) => void;
  cancel: () => void;
  save: (content: string) => Promise<void>;
  /** Put the model's original answer back (drops the edit). */
  restoreOriginal: (message: Message) => Promise<void>;
}

interface Options {
  resetKey: string;
  editMessage: (messageId: number, content: string) => Promise<Message>;
  setPersisted: Dispatch<SetStateAction<Message[]>>;
  confirm: (message: string) => Promise<boolean>;
  onSaved: () => void;
  /** Report a failure that has no open editor to show it in. */
  onError: (message: string) => void;
}

/**
 * Editing an assistant reply in place. The server keeps the model's first
 * answer, so later turns see the edit while the original stays restorable.
 */
export function useReplyEdit({
  resetKey, editMessage, setPersisted, confirm, onSaved, onError,
}: Options): ReplyEditController {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [selection, setSelection] = useState<ReplyEditSelection | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEditingId(null);
    setSelection(null);
    setError(null);
  }, [resetKey]);

  async function write(messageId: number, content: string): Promise<void> {
    const updated = await editMessage(messageId, content);
    setPersisted((prev) => prev.map((m) => (m.id === updated.id ? { ...m, ...updated } : m)));
    onSaved();
  }

  return {
    editingId,
    selection,
    saving,
    error,
    start: (messageId, sel) => {
      setEditingId(messageId);
      setSelection(sel ?? null);
      setError(null);
    },
    cancel: () => {
      setEditingId(null);
      setSelection(null);
      setError(null);
    },
    save: async (content) => {
      if (editingId === null || !content.trim()) return;
      setSaving(true);
      setError(null);
      try {
        await write(editingId, content);
        setEditingId(null);
        setSelection(null);
      } catch (err) {
        setError((err as Error).message || "Saving the edit failed");
      } finally {
        setSaving(false);
      }
    },
    restoreOriginal: async (message) => {
      if (message.original_content == null) return;
      if (!(await confirm("Restore the original reply? Your edit will be lost."))) return;
      try {
        await write(message.id, message.original_content);
        if (editingId === message.id) setEditingId(null);
      } catch (err) {
        onError(`Restoring the reply failed: ${(err as Error).message}`);
      }
    },
  };
}
