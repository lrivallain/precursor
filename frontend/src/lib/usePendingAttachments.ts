import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { api } from "./api";
import {
  splitSupportedAttachmentFiles,
  unsupportedAttachmentMessage,
} from "./attachments";
import type { Attachment } from "./types";

export interface UsePendingAttachmentsOptions {
  /** Identity of the owning conversation; a change flushes orphaned uploads. */
  resetKey: number;
  /** Upload a single file and return the created attachment (topic vs chat). */
  upload: (file: File) => Promise<Attachment>;
}

export interface PendingAttachments {
  pendingAttachments: Attachment[];
  setPendingAttachments: Dispatch<SetStateAction<Attachment[]>>;
  uploadingCount: number;
  attachmentError: string | null;
  uploadFiles: (files: Iterable<File>) => Promise<void>;
  removeAttachment: (id: number) => Promise<void>;
}

/**
 * Manage images/documents the user has uploaded but not yet bound to a sent
 * message. They live as orphan rows server-side until `/messages/stream` binds
 * them, or the user removes them / leaves the conversation — at which point we
 * DELETE them so they don't accumulate. Duplicated verbatim by both panels; the
 * only delta is the per-surface `upload` closure.
 */
export function usePendingAttachments({
  resetKey,
  upload,
}: UsePendingAttachmentsOptions): PendingAttachments {
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const pendingAttachmentsRef = useRef<Attachment[]>([]);

  useEffect(() => {
    pendingAttachmentsRef.current = pendingAttachments;
  }, [pendingAttachments]);

  // On conversation change / unmount, drop unsent attachments and delete them
  // server-side so they don't linger as orphan rows.
  useEffect(() => {
    return () => {
      const orphans = pendingAttachmentsRef.current;
      pendingAttachmentsRef.current = [];
      setPendingAttachments([]);
      setAttachmentError(null);
      for (const a of orphans) {
        void api.attachments.remove(a.id).catch(() => {});
      }
    };
  }, [resetKey]);

  async function uploadFiles(files: Iterable<File>): Promise<void> {
    const { supported, unsupported } = splitSupportedAttachmentFiles(files);
    if (unsupported.length > 0) {
      setAttachmentError(unsupportedAttachmentMessage(unsupported));
    }
    if (supported.length === 0) return;
    if (unsupported.length === 0) setAttachmentError(null);
    setUploadingCount((n) => n + supported.length);
    try {
      for (const file of supported) {
        try {
          const att = await upload(file);
          setPendingAttachments((prev) => [...prev, att]);
        } catch (err) {
          setAttachmentError((err as Error).message || "Upload failed");
        }
      }
    } finally {
      setUploadingCount((n) => Math.max(0, n - supported.length));
    }
  }

  async function removeAttachment(id: number): Promise<void> {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
    try {
      await api.attachments.remove(id);
    } catch {
      // Already gone server-side, or bound to a sent message — nothing to do.
    }
  }

  return {
    pendingAttachments,
    setPendingAttachments,
    uploadingCount,
    attachmentError,
    uploadFiles,
    removeAttachment,
  };
}
