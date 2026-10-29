import { useCallback, useEffect, useState } from "react";
import type { AppliedInfo, ArtifactAction } from "./ArtifactCard";
import type { VersionRow } from "./VersionHistory";
import type { Transfer } from "./WorkTabs";

/**
 * What a discussion's deliverable proposals need from the document they feed
 * (a step's artifact, a plan's final output): its versions, where a proposal's
 * text already lives, and the arrow / "send" button wiring. The document's
 * versions are loaded here once, for both the history and the "applied as vN"
 * labels.
 */
export function useProposals({
  load,
  refreshKey,
  current,
  hasChat,
  send,
  noun,
  sendTooltip,
}: {
  load: () => Promise<VersionRow[]>;
  /** Reload the versions when this changes (the text moved). */
  refreshKey: unknown;
  current: number;
  hasChat: boolean;
  /** Saves the deliverable of a discussion message; resolves true once it landed. */
  send: (messageId: number) => Promise<boolean>;
  /** "artifact", "final output". */
  noun: string;
  sendTooltip: string;
}): { versions: VersionRow[]; transfer: Transfer | undefined; artifactAction: ArtifactAction } {
  const [versions, setVersions] = useState<VersionRow[]>([]);
  const [proposals, setProposals] = useState<{ id: number; artifact: string }[]>([]);

  const register = useCallback((messageId: number, artifact: string) => {
    setProposals((all) => [...all.filter((p) => p.id !== messageId), { id: messageId, artifact }]);
    return () => setProposals((all) => all.filter((p) => p.id !== messageId));
  }, []);

  // Versions are matched by content, so "applied" survives a reload and also
  // covers text that was applied from another window.
  useEffect(() => {
    let cancelled = false;
    load()
      .then((rows) => {
        if (!cancelled) setVersions(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const applied = useCallback(
    (artifact: string): AppliedInfo | null => {
      const text = artifact.trim();
      const match = text ? versions.find((v) => v.content.trim() === text) : undefined;
      return match ? { label: `Applied as v${match.version}`, current: match.version === current } : null;
    },
    [versions, current],
  );

  // The proposal the divider arrow carries: the newest finished reply, unless
  // its text already is the current version.
  const newest = proposals.reduce<{ id: number; artifact: string } | null>((a, p) => (!a || p.id > a.id ? p : a), null);
  const latest = newest && !applied(newest.artifact)?.current ? newest.id : null;

  return {
    versions,
    transfer: hasChat
      ? {
          label: `Send the proposal to the ${noun}`,
          tooltip: `Send the model's latest proposal to the ${noun} as a new version. Your unsaved edits are kept.`,
          idleReason: `Nothing to send yet. This lights up when a reply in the discussion proposes a ${noun}.`,
          ready: latest !== null,
          onClick: () => (latest !== null ? send(latest) : Promise.resolve(false)),
        }
      : undefined,
    artifactAction: {
      label: `Send to ${noun}`,
      tooltip: sendTooltip,
      send: async (id) => void (await send(id)),
      register,
      applied,
    },
  };
}
