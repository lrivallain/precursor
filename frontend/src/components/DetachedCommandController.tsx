import { useState } from "react";
import { CommandDraftCard, type CommandDraftPayload } from "./CommandDraftCard";
import { DetachedWindowPortal } from "./DetachedWindowPortal";
import { api } from "../lib/api";
import type { DetachedSession } from "../lib/detachedDraftStore";

interface Props {
  session: DetachedSession;
  /** Remove the session from the store (which closes the window). */
  onDone: () => void;
}

/**
 * Standalone GitHub draft card (create issue / post update / close issue) that
 * lives in its own browser window and posts against the topic it was popped out
 * from, regardless of what the main app is showing.
 */
export function DetachedCommandController({ session, onDone }: Props) {
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = session.containerId;

  async function post(payload: CommandDraftPayload): Promise<void> {
    setPosting(true);
    setError(null);
    try {
      if (session.kind === "gh-update") {
        await api.postGhUpdate(id, payload.body);
      } else if (session.kind === "gh-create") {
        const title = (payload.title ?? "").trim();
        if (!title) throw new Error("Title is required.");
        await api.postGhCreate(id, title, payload.body);
      } else {
        await api.postGhClose(id, payload.body, "completed");
      }
      onDone();
    } catch (err) {
      setPosting(false);
      setError((err as Error).message);
    }
  }

  return (
    <DetachedWindowPortal title={session.title} onUserClose={onDone}>
      <CommandDraftCard
        variant="embedded"
        title={session.title}
        subtitle={session.subtitle}
        initialBody={session.initialText}
        initialTitle={session.kind === "gh-create" ? (session.initialTitle ?? "") : undefined}
        titleLabel={session.titleLabel}
        bodyPlaceholder={session.bodyPlaceholder}
        bodyRequired={session.bodyRequired}
        posting={posting}
        error={error}
        sendLabel={session.sendLabel}
        postingLabel={session.postingLabel}
        confirmHint={session.confirmHint}
        onSend={post}
        onCancel={onDone}
      />
    </DetachedWindowPortal>
  );
}
