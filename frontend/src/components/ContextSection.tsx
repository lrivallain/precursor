import { CalendarClock, FileText } from "lucide-react";
import type { MeetingSession } from "../lib/types";

interface Props {
  session: MeetingSession;
  topicTitle: string | null;
}

/**
 * Context tab. Phase B fills this with an AI summary of the attached topic's
 * conversation and an "attach a meeting from your M365 agenda" flow (via the
 * WorkIQ MCP). For now it shows the linked topic and what's coming.
 */
export function ContextSection({ session, topicTitle }: Props) {
  return (
    <div className="flex h-full flex-col overflow-y-auto p-4">
      <section className="mb-4">
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
          <FileText size={12} /> Topic context
        </div>
        {session.topic_id != null ? (
          <p className="text-sm text-text">
            Linked to <strong>{topicTitle ?? "a topic"}</strong>. An AI summary of
            its conversation will appear here.
          </p>
        ) : (
          <p className="text-sm text-muted">
            No topic attached. Pick one from the toolbar to bring its context into
            the assistant.
          </p>
        )}
      </section>

      <section>
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
          <CalendarClock size={12} /> Meeting from your agenda
        </div>
        <p className="text-sm text-muted">
          Attaching a meeting from your Microsoft 365 agenda (via WorkIQ) — to
          pull in the invitees and details — is coming next.
        </p>
      </section>
    </div>
  );
}
