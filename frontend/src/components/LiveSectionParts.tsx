import { ExternalLink, MessagesSquare } from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { InlineTitle } from "./InlineTitle";
import { LiveList } from "./LiveList";
import { LiveStartHero } from "./LiveStartHero";
import { LiveView } from "./LiveView";
import { liveUrl, navigate } from "../lib/routes";
import { findNode } from "../lib/topicTree";
import type { Collection, TopicNode } from "../lib/types";
import type { LiveSessionsController } from "../lib/useLiveSessionsController";

// The live section's pieces of the app shell. Each one renders into a slot App
// owns (header, main pane, sidebar, home launcher) and reads its state from the
// live sessions controller. `tree` and `collections` still come from App's
// topics state.

// The shared header's live branch.
export function LiveHeader({
  controller,
  tree,
  globalGithubRepo,
  onOpenTopic,
}: {
  controller: LiveSessionsController;
  tree: TopicNode[];
  globalGithubRepo: string;
  onOpenTopic: (topicId: number) => void;
}) {
  const { activeSession, handleRenameSession } = controller;
  if (!activeSession) {
    return <span className="truncate font-medium min-w-0 flex-1">Live</span>;
  }
  const liveTopic =
    activeSession.topic_id != null
      ? findNode(tree, activeSession.topic_id)
      : null;
  const liveIssueNumber = liveTopic?.github_issue_number ?? null;
  const liveIssueRepo =
    liveTopic?.github_repo || globalGithubRepo || "";
  return (
    <>
      <InlineTitle
        title={activeSession.title}
        onRename={(t) => handleRenameSession(activeSession, t)}
        className="truncate font-medium min-w-0 flex-1"
        inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
      />
      {liveTopic && (
        <button
          type="button"
          onClick={() => onOpenTopic(liveTopic.id)}
          className="p-2 rounded text-sky-600 hover:bg-surface shrink-0 dark:text-sky-400"
          aria-label={`Open topic: ${liveTopic.title}`}
          data-tooltip={`Open topic: ${liveTopic.title}`}
        >
          <MessagesSquare size={18} />
        </button>
      )}
      {liveIssueNumber != null && liveIssueRepo && (
        <a
          href={`https://github.com/${liveIssueRepo}/issues/${liveIssueNumber}`}
          target="_blank"
          rel="noreferrer"
          className="group inline-flex items-center gap-1 p-2 rounded hover:bg-surface shrink-0"
          aria-label={`Open issue #${liveIssueNumber} on GitHub`}
          data-tooltip={`Open issue #${liveIssueNumber} on GitHub`}
        >
          <Github size={18} />
          <ExternalLink
            size={11}
            className="opacity-60 transition group-hover:opacity-100"
          />
        </a>
      )}
    </>
  );
}

// The main pane's live branch: the open session, or the start hero.
export function LiveMain({
  controller,
  tree,
  collections,
}: {
  controller: LiveSessionsController;
  tree: TopicNode[];
  collections: Collection[];
}) {
  const {
    activeSession,
    setMeetingSessions,
    setActiveSessionId,
    setLiveRecordingId,
    loadMeetingSessions,
  } = controller;
  return activeSession ? (
    <LiveView
      key={activeSession.id}
      session={activeSession}
      topics={tree}
      collections={collections}
      onUpdated={(updated) =>
        setMeetingSessions((prev) =>
          prev ? prev.map((s) => (s.id === updated.id ? updated : s)) : prev,
        )
      }
      onDeleted={async () => {
        const list = await loadMeetingSessions();
        setActiveSessionId(null);
        navigate(liveUrl(null));
        void list;
      }}
      onArchived={async () => {
        await loadMeetingSessions();
        setActiveSessionId(null);
        navigate(liveUrl(null));
      }}
      onRecordingChange={setLiveRecordingId}
    />
  ) : (
    <LiveStartHero
      topics={tree}
      collections={collections}
      onCreated={async (session) => {
        await loadMeetingSessions();
        setActiveSessionId(session.id);
        navigate(liveUrl(session));
      }}
    />
  );
}

// The home launcher's "New live session" card: the start form, never a
// selection.
export function LiveHomeSurface({
  controller,
  tree,
  collections,
}: {
  controller: LiveSessionsController;
  tree: TopicNode[];
  collections: Collection[];
}) {
  return (
    <LiveStartHero topics={tree} collections={collections} onCreated={controller.createLiveFromHome} />
  );
}

// The sidebar's live slot.
export function LiveSidebarList({ controller }: { controller: LiveSessionsController }) {
  const {
    meetingSessions,
    activeSessionId,
    liveRecordingId,
    handleSelectSession,
    handleRenameSession,
    handleArchiveSessions,
  } = controller;
  return (
    <LiveList
      sessions={meetingSessions}
      activeId={activeSessionId}
      recordingId={liveRecordingId}
      onSelect={handleSelectSession}
      onRename={handleRenameSession}
      onArchiveMany={handleArchiveSessions}
    />
  );
}
