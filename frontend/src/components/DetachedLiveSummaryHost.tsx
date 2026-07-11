import { liveSummaryStore, useDetachedSummaries } from "../lib/liveSummaryStore";
import { DetachedLiveSummaryController } from "./DetachedLiveSummaryController";

/**
 * App-level host for meeting-summary panels popped out into their own browser
 * window. Mounted once near the app root so the windows survive navigation away
 * from the live session in the main tab.
 */
export function DetachedLiveSummaryHost() {
  const sessions = useDetachedSummaries();
  return (
    <>
      {sessions.map((session) => (
        <DetachedLiveSummaryController
          key={session.id}
          session={session}
          onDone={() => liveSummaryStore.close(session.id)}
        />
      ))}
    </>
  );
}
