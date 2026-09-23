import {
  ChevronRight,
  FileText,
  Pin,
  PinOff,
  Settings as SettingsIcon,
} from "lucide-react";
import { ChatPanel } from "./ChatPanel";
import { InlineTitle } from "./InlineTitle";
import { IssueStatusBadge } from "./IssueStatusBadge";
import { IssueLabelChip, IssueStateBadge } from "./IssueTags";
import { TopicStartHero } from "./StartHero";
import { TopicSettingsPanel } from "./TopicSettingsPanel";
import { api } from "../lib/api";
import { toggleTopicSummary } from "../lib/summaryOpen";
import { topicAncestors } from "../lib/topicTree";
import type { TopicsController } from "../lib/useTopicsController";

// The topics section's pieces of the app shell. Each one renders into a slot
// App owns (header, main pane, modal layer, home launcher) and reads its state
// from the topics controller. Reminders, roles and the issue-associations
// setting still come from App; the topic tree itself is still drawn by
// `Sidebar` from App's props.

// The shared header's topics branch: breadcrumb, title, issue badges, and the
// summary / pin / settings buttons.
export function TopicsHeader({
  controller,
  issueAssociationsEnabled,
}: {
  controller: TopicsController;
  issueAssociationsEnabled: boolean;
}) {
  const {
    tree,
    activeTopic,
    issueContext,
    handleSelect,
    handleRenameTopic,
    openTopicSettings,
    togglePin,
  } = controller;
  return (
    <>
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {activeTopic ? (
          <>
            {topicAncestors(tree, activeTopic.id).map((anc) => (
              <div key={anc.id} className="flex items-center gap-2 min-w-0 shrink">
                <button
                  type="button"
                  onClick={() => void handleSelect(anc.id)}
                  className="max-w-[10rem] truncate text-sm text-muted hover:text-fg hover:underline"
                  title={anc.title}
                  data-tooltip={`Go to ${anc.title}`}
                >
                  {anc.title}
                </button>
                <ChevronRight size={14} className="shrink-0 text-muted" />
              </div>
            ))}
            <InlineTitle
              title={activeTopic.title}
              onRename={(t) => handleRenameTopic(activeTopic.id, t)}
              className="truncate font-medium"
              inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
            />
          </>
        ) : (
          <span className="truncate font-medium">Select or create a topic</span>
        )}
        {activeTopic && issueAssociationsEnabled && (
          <IssueStatusBadge
            status={issueContext.status}
            onClick={() => openTopicSettings("context")}
          />
        )}
      </div>
      {activeTopic &&
        issueAssociationsEnabled &&
        issueContext.summary && (
          <div className="flex items-center gap-1.5 flex-wrap justify-end min-w-0">
            <IssueStateBadge state={issueContext.summary.issue_state} />
            {issueContext.summary.labels.map((label) => (
              <IssueLabelChip key={label.name} label={label} />
            ))}
          </div>
        )}
      {activeTopic && (
        <button
          className="p-2 rounded hover:bg-surface shrink-0"
          aria-label="Toggle topic summary"
          data-tooltip={"Topic summary\nStatus, open actions and key information"}
          onClick={() => toggleTopicSummary(activeTopic.id)}
        >
          <FileText size={18} />
        </button>
      )}
      {activeTopic && (
        <button
          className="p-2 rounded hover:bg-surface shrink-0"
          aria-label={activeTopic.pinned ? "Unpin topic" : "Pin topic"}
          data-tooltip={activeTopic.pinned ? "Unpin topic" : "Pin topic"}
          onClick={togglePin}
        >
          {activeTopic.pinned ? (
            <PinOff size={18} className="text-accent" />
          ) : (
            <Pin size={18} />
          )}
        </button>
      )}
      {activeTopic && (
        <button
          className="p-2 rounded hover:bg-surface shrink-0"
          aria-label="Topic settings"
          data-tooltip="Topic settings"
          onClick={() => openTopicSettings("settings")}
        >
          <SettingsIcon size={18} />
        </button>
      )}
    </>
  );
}

// The main pane's topics branch: the open topic, or the inline create form.
export function TopicsMain({
  controller,
  onRemindersChanged,
  onSetRole,
}: {
  controller: TopicsController;
  onRemindersChanged: () => void;
  onSetRole: (roleId: number | null) => Promise<void>;
}) {
  const {
    tree,
    activeTopic,
    setActiveTopic,
    activeCollectionId,
    topicDraftParentId,
    topicDraftNonce,
    chatReloadKey,
    refreshTree,
    handleTopicCreated,
  } = controller;
  return activeTopic ? (
    <ChatPanel
      key={`${activeTopic.id}:${chatReloadKey}`}
      topic={activeTopic}
      onTopicUpdated={async () => {
        // Commands persist messages server-side; clear the badge
        // for the topic the user is actively viewing before
        // re-loading the tree so its unread count doesn't tick up.
        // Also re-fetch the active topic itself — some commands
        // (e.g. /gh-create) mutate topic fields like the linked
        // issue number, and the chat header needs to reflect that.
        try {
          await api.topics.markRead(activeTopic.id);
        } catch {
          // non-fatal
        }
        try {
          setActiveTopic(await api.topics.get(activeTopic.id));
        } catch {
          // non-fatal
        }
        await refreshTree();
      }}
      onArchived={async () => {
        // /archive removes the topic from the active view; drop the
        // selection and refresh the tree so it moves to the archive.
        setActiveTopic(null);
        await refreshTree();
      }}
      onNavigateTopic={async (topic) => {
        // /new created a child topic — switch to it and refresh so it
        // appears in the tree.
        setActiveTopic(topic);
        await refreshTree();
      }}
      onRemindersChanged={onRemindersChanged}
      onSetRole={onSetRole}
    />
  ) : (
    <TopicStartHero
      key={`topic-create-${topicDraftParentId ?? "root"}-${topicDraftNonce}`}
      tree={tree}
      initialParentId={topicDraftParentId}
      collectionId={activeCollectionId}
      onCreated={handleTopicCreated}
    />
  );
}

// The home launcher's "New topic" card: the create form, never a selection.
export function TopicsHomeSurface({ controller }: { controller: TopicsController }) {
  const { tree, activeCollectionId, handleTopicCreated } = controller;
  return (
    <TopicStartHero
      tree={tree}
      collectionId={activeCollectionId}
      onCreated={handleTopicCreated}
    />
  );
}

// The topic settings drawer, opened from the header's settings button or its
// issue status badge.
export function TopicsSettingsModal({ controller }: { controller: TopicsController }) {
  const {
    activeTopic,
    setActiveTopic,
    collectionTree,
    issueContext,
    topicSettingsOpen,
    setTopicSettingsOpen,
    topicSettingsTab,
    setChatReloadKey,
    refreshTree,
  } = controller;
  if (!topicSettingsOpen || !activeTopic) return null;
  return (
    <TopicSettingsPanel
      topic={activeTopic}
      tree={collectionTree}
      context={issueContext}
      initialTab={topicSettingsTab}
      onClose={() => setTopicSettingsOpen(false)}
      onSaved={async (updated) => {
        setActiveTopic(updated);
        setTopicSettingsOpen(false);
        await refreshTree();
      }}
      onDeleted={async () => {
        setTopicSettingsOpen(false);
        setActiveTopic(null);
        await refreshTree();
      }}
      onCleared={() => {
        setChatReloadKey((k) => k + 1);
        setTopicSettingsOpen(false);
      }}
    />
  );
}
