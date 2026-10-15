import { useCallback, useEffect, useRef } from "react";
import type { ContainerApi } from "./api";
import { eventBus } from "./events";
import { streamStore, useStreamVersion, type ConvKind } from "./streamStore";
import type { ToolApproval, ToolApprovalDecision } from "./types";

export interface ApprovalsController {
  /** Tool calls the turn is parked on, plus decided ones about to run. */
  approvals: ToolApproval[];
  /** Answer one approval; the turn resumes once the last one is answered. */
  decide: (approval: ToolApproval, decision: ToolApprovalDecision) => Promise<void>;
}

interface Options {
  kind: ConvKind;
  id: number;
  streamKey: string;
  containerApi: ContainerApi;
  streaming: boolean;
  /** Refresh the sidebar badge after the pending count moved. */
  onUpdated: () => void;
}

/**
 * The approvals a conversation is parked on. They live server-side, so a reload
 * (or another window) finds them again; a turn whose answers were all recorded
 * but never resumed (the window closed first) is resumed on load.
 */
export function useApprovals({
  kind,
  id,
  streamKey,
  containerApi,
  streaming,
  onUpdated,
}: Options): ApprovalsController {
  useStreamVersion();
  const approvals = streamStore.approvals(streamKey);
  const onUpdatedRef = useRef(onUpdated);
  onUpdatedRef.current = onUpdated;
  // The safety-net resume runs once per conversation, on its first load.
  const settledRef = useRef<string | null>(null);

  const refresh = useCallback(
    async (resumeStranded: boolean) => {
      try {
        const rows = await containerApi.listApprovals();
        streamStore.setApprovals(streamKey, rows);
        const stranded =
          rows.length > 0 && rows.every((r) => r.status !== "pending" && !r.executed);
        if (resumeStranded && stranded && !streamStore.isStreaming(streamKey)) {
          void streamStore.resume(streamKey);
        }
      } catch {
        // non-fatal: the card list just stays as it was
      }
    },
    [containerApi, streamKey],
  );

  useEffect(() => {
    const first = settledRef.current !== streamKey;
    settledRef.current = streamKey;
    void refresh(first);
  }, [refresh, streamKey]);

  // A turn that parked or finished changes what is waiting.
  useEffect(() => {
    if (!streaming) void refresh(false);
  }, [streaming, refresh]);

  useEffect(() => {
    eventBus.start();
    return eventBus.subscribe((event) => {
      if (event.type !== "approval.changed") return;
      const mine = kind === "topic" ? event.topic_id === id : event.chat_id === id;
      if (mine) void refresh(false);
    });
  }, [kind, id, refresh]);

  const decide = useCallback(
    async (approval: ToolApproval, decision: ToolApprovalDecision) => {
      const result = await containerApi.decideApproval(approval.id, decision);
      const next = streamStore
        .approvals(streamKey)
        .map((r) => (r.id === approval.id ? result.approval : r));
      streamStore.setApprovals(streamKey, next);
      onUpdatedRef.current();
      if (result.pending === 0) void streamStore.resume(streamKey);
    },
    [containerApi, streamKey],
  );

  return { approvals, decide };
}
