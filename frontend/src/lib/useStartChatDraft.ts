import { useEffect, useRef, useState } from "react";
import type { ComposerAttachments } from "../components/Composer";
import { api } from "./api";
import { splitSupportedAttachmentFiles, unsupportedAttachmentMessage } from "./attachments";
import type { Attachment, Chat } from "./types";

export interface StartChatDraft {
  /** The chat created to hold the uploads; null until the first attachment. */
  chat: Chat | null;
  attachments: ComposerAttachments;
  /** Mark the draft as handed to a send, so leaving the screen keeps it. */
  release: () => void;
  /** Take the draft back after a send that failed. */
  reclaim: () => void;
  /** Forget the draft once its chat and attachments belong to a sent message. */
  reset: () => void;
}

/**
 * Attachments for the "Start a new chat" composer. Uploads need a container, so
 * the first attachment creates the chat that the first message will then use. A
 * draft that is never sent is deleted (attachments first: SQLite doesn't cascade
 * them) when the start screen goes away.
 */
export function useStartChatDraft(roleName: string | null): StartChatDraft {
  const [chat, setChat] = useState<Chat | null>(null);
  const [pending, setPending] = useState<Attachment[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const chatRef = useRef<Chat | null>(null);
  const creatingRef = useRef<Promise<Chat> | null>(null);
  const pendingRef = useRef<Attachment[]>([]);
  const roleRef = useRef(roleName);
  const releasedRef = useRef(false);
  roleRef.current = roleName;
  pendingRef.current = pending;

  function ensureChat(): Promise<Chat> {
    if (chatRef.current) return Promise.resolve(chatRef.current);
    if (!creatingRef.current) {
      creatingRef.current = api.chats
        .create({ title: "New chat", autoname: true, role: roleRef.current })
        .then((c) => {
          chatRef.current = c;
          setChat(c);
          return c;
        })
        .finally(() => {
          creatingRef.current = null;
        });
    }
    return creatingRef.current;
  }

  useEffect(
    () => () => {
      if (releasedRef.current) return;
      const orphans = pendingRef.current;
      const draft = chatRef.current;
      if (!draft) return;
      void (async () => {
        for (const a of orphans) await api.attachments.remove(a.id).catch(() => {});
        await api.chats.remove(draft.id).catch(() => {});
      })();
    },
    [],
  );

  async function onFiles(files: Iterable<File>): Promise<void> {
    const { supported, unsupported } = splitSupportedAttachmentFiles(files);
    if (unsupported.length > 0) setError(unsupportedAttachmentMessage(unsupported));
    if (supported.length === 0) return;
    if (unsupported.length === 0) setError(null);
    setUploadingCount((n) => n + supported.length);
    try {
      const target = await ensureChat();
      for (const file of supported) {
        try {
          const att = await api.attachments.uploadForChat(target.id, file);
          setPending((prev) => [...prev, att]);
        } catch (err) {
          setError((err as Error).message || "Upload failed");
        }
      }
    } catch (err) {
      setError((err as Error).message || "Upload failed");
    } finally {
      setUploadingCount((n) => Math.max(0, n - supported.length));
    }
  }

  async function onRemove(id: number): Promise<void> {
    setPending((prev) => prev.filter((a) => a.id !== id));
    await api.attachments.remove(id).catch(() => {});
  }

  return {
    chat,
    attachments: {
      pending,
      uploadingCount,
      error,
      onFiles: (files) => void onFiles(files),
      onRemove: (id) => void onRemove(id),
    },
    release: () => {
      releasedRef.current = true;
    },
    reclaim: () => {
      releasedRef.current = false;
    },
    reset: () => {
      chatRef.current = null;
      pendingRef.current = [];
      setChat(null);
      setPending([]);
      setError(null);
    },
  };
}
