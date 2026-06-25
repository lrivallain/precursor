import { detachedDraftStore, useDetachedDrafts } from "../lib/detachedDraftStore";
import { DetachedNotesController } from "./DetachedNotesController";
import { DetachedCommandController } from "./DetachedCommandController";

/**
 * App-level host for panels that have been popped out into their own browser
 * window. Mounted once near the app root so the windows it owns survive topic /
 * chat navigation in the main tab. Each session renders its own controller,
 * which owns the draft state and keeps acting on the original conversation.
 */
export function DetachedDraftHost() {
  const sessions = useDetachedDrafts();
  return (
    <>
      {sessions.map((session) =>
        session.kind === "notes" ? (
          <DetachedNotesController
            key={session.id}
            session={session}
            onDone={() => detachedDraftStore.close(session.id)}
          />
        ) : (
          <DetachedCommandController
            key={session.id}
            session={session}
            onDone={() => detachedDraftStore.close(session.id)}
          />
        ),
      )}
    </>
  );
}
