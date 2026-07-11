import { useRef, useState } from "react";
import { SummaryPanel } from "./SummaryPanel";
import { DetachedWindowPortal } from "./DetachedWindowPortal";
import { api } from "../lib/api";
import type { DetachedSummary } from "../lib/liveSummaryStore";

interface Props {
  session: DetachedSummary;
  /** Remove the session from the store (which closes the window). */
  onDone: () => void;
}

/**
 * A meeting summary that lives in its own browser window and keeps acting on the
 * live session it was popped out from — even after the user navigates elsewhere
 * in the main app. State lives here (in the persistent app-level host).
 */
export function DetachedLiveSummaryController({ session, onDone }: Props) {
  const [text, setText] = useState(session.initialText);
  const latest = useRef(text);
  latest.current = text;

  return (
    <DetachedWindowPortal title={session.title} onUserClose={onDone}>
      <SummaryPanel
        variant="embedded"
        windowStorageKey="precursor:live-summary:window"
        title={session.title}
        text={text}
        onTextChange={setText}
        canPost={session.topicId != null}
        topicTitle={session.topicTitle}
        onPost={(t) => api.postMeetingSummary(session.sessionId, t).then(() => undefined)}
        onClose={onDone}
      />
    </DetachedWindowPortal>
  );
}
