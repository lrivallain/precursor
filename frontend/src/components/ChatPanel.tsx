import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowRightCircle } from "lucide-react";
import { AgentExchangeBadge } from "./MessageBubble";
import { CommandDraftCard, type CommandDraftPayload } from "./CommandDraftCard";
import { ConversationNotes, NotesConfirmModal } from "./ConversationNotes";
import { TranscriptMessage, TranscriptTail, UndoDeleteToasts } from "./ConversationTranscript";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { ChatStatsPanel } from "./ChatStatsPanel";
import { api } from "../lib/api";
import { GITHUB_SLASH_COMMANDS } from "../lib/commands";
import { detachedDraftStore } from "../lib/detachedDraftStore";
import { useSettings } from "../lib/settingsStore";
import { useResizableWidth } from "../lib/useResizableWidth";
import { useResizableHeight } from "../lib/useResizableHeight";
import { useComposerInput } from "../lib/useComposerInput";
import { useConversation } from "../lib/useConversation";
import { ResizeHandle } from "./ResizeHandle";
import { ReminderModal } from "./ReminderModal";
import { ReminderBanner } from "./ReminderBanner";
import { useTopicSummary } from "../lib/useTopicSummary";
import { subscribeTopicSummaryToggle } from "../lib/summaryOpen";
import { TopicSummaryPanel } from "./TopicSummaryPanel";
import type {
  AgentSession,
  Message,
  Topic,
} from "../lib/types";
import { RoleSelector } from "./RoleSelector";

interface ChatPanelProps {
  topic: Topic;
  onTopicUpdated: () => void;
  /** Called after the topic is archived via the /archive command. */
  onArchived?: () => void;
  /** Switch the active topic (used by the /new command after creating one). */
  onNavigateTopic?: (topic: Topic) => void;
  /** Refresh the sidebar Reminders section after a set / cancel / done. */
  onRemindersChanged?: () => void;
  /** Persist a role change for this topic (null = default). */
  onSetRole?: (roleId: number | null) => Promise<void>;
}

type PendingKind = "gh-update" | "gh-create" | "gh-close";

interface PendingCommand {
  kind: PendingKind;
  loading: boolean;
  posting: boolean;
  body: string;
  title?: string;
  repo: string | null;
  issueNumber: number | null;
  error: string | null;
}

function cardTitle(p: PendingCommand): string {
  switch (p.kind) {
    case "gh-update":
      return "Comment on GitHub issue";
    case "gh-create":
      return "Create GitHub issue";
    case "gh-close":
      return "Close GitHub issue";
  }
}

function cardSubtitle(p: PendingCommand): string | undefined {
  if (p.kind === "gh-create") return p.repo ?? undefined;
  if (p.repo && p.issueNumber) return `${p.repo}#${p.issueNumber}`;
  return undefined;
}

function cardSendLabel(kind: PendingKind): string {
  switch (kind) {
    case "gh-update":
      return "Post comment";
    case "gh-create":
      return "Create issue";
    case "gh-close":
      return "Close issue";
  }
}

function cardPostingLabel(kind: PendingKind): string {
  switch (kind) {
    case "gh-update":
      return "Posting…";
    case "gh-create":
      return "Creating…";
    case "gh-close":
      return "Closing…";
  }
}

function cardBodyPlaceholder(kind: PendingKind): string {
  return kind === "gh-close"
    ? "Optional closing comment in GitHub-Flavored Markdown… (leave empty to close without a comment)"
    : "Write in GitHub-Flavored Markdown…";
}

function cardConfirmHint(kind: PendingKind): string | undefined {
  if (kind === "gh-close") return "This will close the issue on GitHub.";
  if (kind === "gh-create") return "A new issue will be created and linked to this topic.";
  return undefined;
}

export function ChatPanel({ topic, onTopicUpdated, onArchived, onNavigateTopic, onRemindersChanged, onSetRole }: ChatPanelProps) {
  const [composerFocusToken, setComposerFocusToken] = useState(0);
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null);

  const settings = useSettings();
  const showStats = settings?.show_chat_stats ?? true;
  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;
  const agentsEnabled = settings?.agents_enabled ?? false;
  const excludedCommands = useMemo<ReadonlySet<string>>(() => {
    const set = new Set<string>(issueAssociationsEnabled ? [] : GITHUB_SLASH_COMMANDS);
    if (!agentsEnabled) set.add("agent");
    return set;
  }, [issueAssociationsEnabled, agentsEnabled]);
  const composer = useComposerInput({ surface: "topic", exclude: excludedCommands });
  const conv = useConversation({
    kind: "topic",
    id: topic.id,
    composer,
    onCommand: dispatchCommand,
    onUpdated: onTopicUpdated,
    onRemindersChanged,
    onSetRole,
    onPostComment: async (text, attachmentIds) => {
      const res = await api.github.postUpdate(topic.id, text, attachmentIds);
      return [
        ...(res.local_note_message ? [res.local_note_message] : []),
        res.message,
      ];
    },
  });
  const { streamKey, setPersisted, systemNote, visibleMessages, streaming, reminders } = conv;

  const summary = useTopicSummary(topic.id);
  const toggleSummary = summary.toggleVisible;
  // The panel is reached from the shared topic header, which doesn't own its
  // state — see lib/summaryOpen.ts.
  useEffect(
    () => subscribeTopicSummaryToggle(topic.id, () => void toggleSummary()),
    [topic.id, toggleSummary],
  );

  const { width: chatWidth, onMouseDown: onChatResize } = useResizableWidth({
    storageKey: "precursor:chat:width",
    defaultWidth: 768,
    min: 480,
    max: 1400,
  });

  const { height: composerHeight, onMouseDown: onComposerResize } =
    useResizableHeight({
      storageKey: "precursor:composer:height",
      defaultHeight: 56,
      min: 40,
      max: 480,
    });

  async function dispatchCommand(name: string, argument: string): Promise<void> {
    if (name === "gh-sync") {
      await runGhSync();
      return;
    }
    if (name === "rename") {
      await runRename(argument);
      return;
    }
    if (name === "suggest-name") {
      await runSuggestName();
      return;
    }
    if (name === "new") {
      await runNew(argument);
      return;
    }
    if (name === "pin" || name === "unpin") {
      await runSetPinned(name === "pin");
      return;
    }
    if (name === "archive") {
      await runArchive();
      return;
    }
    if (name === "show-summary" || name === "hide-summary") {
      await summary.setVisible(name === "show-summary");
      return;
    }
    if (name === "update-summary") {
      await summary.refresh(argument || undefined);
      return;
    }
    if (name === "todo-summary" || name === "important-summary") {
      if (!argument.trim()) {
        systemNote(
          name === "todo-summary"
            ? "Usage: `/todo-summary <action>`"
            : "Usage: `/important-summary <information>`",
        );
        return;
      }
      await summary.addItem(
        name === "todo-summary" ? "todo" : "important",
        argument.trim(),
      );
      return;
    }
    if (name === "collection") {
      await runCollection(argument);
      return;
    }
    if (name === "agent") {
      await runAgent(argument);
      return;
    }
    if (name === "gh-update" || name === "gh-create" || name === "gh-close") {
      await startDraft(name, argument);
    }
  }

  async function runCollection(argument: string): Promise<void> {
    const arg = argument.trim();
    let collections;
    try {
      collections = await api.collections.list();
    } catch (err) {
      systemNote(`Could not load collections: ${(err as Error).message}`);
      return;
    }
    if (!arg) {
      const current = collections.find((c) => c.id === topic.collection_id);
      systemNote(
        `Collections: ${collections.map((c) => c.name).join(", ")}.` +
          (current ? ` This topic is in "${current.name}".` : ""),
      );
      return;
    }
    const target = collections.find(
      (c) => c.name.toLowerCase() === arg.toLowerCase() || c.slug === arg.toLowerCase(),
    );
    if (!target) {
      systemNote(`Unknown collection "${arg}". Manage collections in Settings → Collections.`);
      return;
    }
    try {
      const updated = await api.topics.update(topic.id, { collection_id: target.id });
      onTopicUpdated();
      systemNote(
        `Moved to "${target.name}" (sub-topics follow).` +
          (topic.parent_id !== null && updated.parent_id === null
            ? " Promoted to a top-level topic — a subtree can't span collections."
            : ""),
      );
    } catch (err) {
      systemNote(`Move failed: ${(err as Error).message}`);
    }
  }

  // Prefill the composer with "/agent <uuid> " so the user can type a follow-up
  // and reinstantiate an existing agent session straight from its summary.
  function prefillAgentFollowUp(ref: string): void {
    composer.setDraft(`/agent ${ref} `);
    setComposerFocusToken((t) => t + 1);
  }

  // Navigate to the Agents tab. A non-null id opens that session; null opens
  // the new-agent form with this topic preselected (via the event's topicId).
  function openAgent(id: number | null): void {
    window.dispatchEvent(
      new CustomEvent("precursor:open-agent", { detail: { id, topicId: topic.id } }),
    );
  }

  async function runAgent(argument: string): Promise<void> {
    const arg = argument.trim();
    // "/agent <session-id> <prompt>" continues an existing session when the
    // first token is a session id — a public UUID (preferred) or a legacy
    // integer — that maps to a real agent. Anything else is treated as a brand
    // new task, so ordinary prompts still work.
    const m = arg.match(
      /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)\b\s*([\s\S]*)$/i,
    );
    if (m) {
      const ref = m[1];
      const prompt = m[2].trim();
      let existing: AgentSession | null = null;
      try {
        existing = await api.agents.get(ref);
      } catch {
        existing = null;
      }
      if (existing) {
        try {
          if (prompt) await api.agents.send(existing.id, prompt);
        } catch (err) {
          systemNote(`Couldn't message "${existing.title}": ${(err as Error).message}`);
          return;
        }
        systemNote(
          prompt
            ? `Sent a follow-up to "${existing.title}".`
            : `Opening "${existing.title}".`,
        );
        openAgent(existing.id);
        return;
      }
      // Not a real session id — fall through and treat the text as a new task.
    }

    if (!arg) {
      // No prompt: open the new-agent form with this topic preselected.
      openAgent(null);
      return;
    }
    try {
      const created = await api.agents.create({ task: arg, topic_id: topic.id });
      systemNote(`Started agent "${created.title}".`);
      openAgent(created.id);
    } catch (err) {
      systemNote(`Couldn't start the agent: ${(err as Error).message}`);
    }
  }

  async function runRename(argument: string): Promise<void> {
    const title = argument.trim();
    if (!title) {
      systemNote("Usage: `/rename <new title>`");
      return;
    }
    try {
      await api.topics.update(topic.id, { title });
      onTopicUpdated();
    } catch (err) {
      systemNote(`Rename failed: ${(err as Error).message}`);
    }
  }

  async function runSuggestName(): Promise<void> {
    try {
      const { title } = await api.topics.suggestName(topic.id);
      onTopicUpdated();
      systemNote(
        title
          ? `Renamed this topic to "${title}".`
          : "Could not suggest a name; the title is unchanged.",
      );
    } catch (err) {
      systemNote(`Suggest name failed: ${(err as Error).message}`);
    }
  }

  async function runNew(argument: string): Promise<void> {
    const title = argument.trim();
    if (!title) {
      systemNote("Usage: `/new <title>`");
      return;
    }
    try {
      const created = await api.topics.create({ title, parent_id: topic.id });
      onNavigateTopic?.(created);
    } catch (err) {
      systemNote(`Create failed: ${(err as Error).message}`);
    }
  }

  async function runSetPinned(pinned: boolean): Promise<void> {
    if (topic.pinned === pinned) {
      systemNote(pinned ? "Already pinned." : "Not pinned.");
      return;
    }
    try {
      await api.topics.update(topic.id, { pinned });
      onTopicUpdated();
    } catch (err) {
      systemNote(`${pinned ? "Pin" : "Unpin"} failed: ${(err as Error).message}`);
    }
  }

  async function runArchive(): Promise<void> {
    try {
      await api.topics.archive(topic.id);
      onArchived?.();
    } catch (err) {
      systemNote(`Archive failed: ${(err as Error).message}`);
    }
  }

  async function runGhSync(): Promise<void> {
    // No editable card — sync is a fire-and-forget refresh. Show a transient
    // placeholder in the message list so the user gets immediate feedback.
    const placeholderId = -Date.now();
    setPersisted((prev) => [
      ...prev,
      {
        id: placeholderId,
        topic_id: topic.id,
        role: "system",
        content: "Syncing linked GitHub issue…",
        tool_calls: null,
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      const res = await api.github.sync(topic.id);
      setPersisted((prev) =>
        prev.filter((m) => m.id !== placeholderId).concat(res.message),
      );
      onTopicUpdated();
    } catch (err) {
      setPersisted((prev) =>
        prev.map((m) =>
          m.id === placeholderId
            ? { ...m, content: `Sync failed: ${(err as Error).message}` }
            : m,
        ),
      );
    }
  }

  async function startDraft(kind: PendingKind, argument: string): Promise<void> {
    const seed: PendingCommand = {
      kind,
      loading: true,
      posting: false,
      body: "",
      title: kind === "gh-create" ? "" : undefined,
      repo: null,
      issueNumber: null,
      error: null,
    };
    setPendingCommand(seed);

    try {
      if (kind === "gh-update") {
        const res = await api.github.draftUpdate(topic.id, argument || undefined);
        setPendingCommand((prev) =>
          prev?.kind === kind
            ? {
                ...prev,
                loading: false,
                body: res.draft,
                repo: res.repo,
                issueNumber: res.issue_number,
              }
            : prev,
        );
      } else if (kind === "gh-create") {
        const res = await api.github.draftCreate(topic.id, argument || undefined);
        setPendingCommand((prev) =>
          prev?.kind === kind
            ? {
                ...prev,
                loading: false,
                title: res.title,
                body: res.body,
                repo: res.repo,
              }
            : prev,
        );
      } else if (kind === "gh-close") {
        const res = await api.github.draftClose(topic.id, argument || undefined);
        setPendingCommand((prev) =>
          prev?.kind === kind
            ? {
                ...prev,
                loading: false,
                body: res.draft,
                repo: res.repo,
                issueNumber: res.issue_number,
              }
            : prev,
        );
      }
    } catch (err) {
      setPendingCommand((prev) =>
        prev?.kind === kind
          ? { ...prev, loading: false, error: (err as Error).message }
          : prev,
      );
    }
  }

  async function postCommandDraft(payload: CommandDraftPayload): Promise<void> {
    if (!pendingCommand) return;
    const kind = pendingCommand.kind;
    setPendingCommand((prev) =>
      prev ? { ...prev, posting: true, error: null } : prev,
    );
    try {
      let message: Message;
      if (kind === "gh-update") {
        const res = await api.github.postUpdate(topic.id, payload.body);
        message = res.message;
      } else if (kind === "gh-create") {
        const title = (payload.title ?? "").trim();
        if (!title) throw new Error("Title is required.");
        const res = await api.github.postCreate(topic.id, title, payload.body);
        message = res.message;
      } else {
        // gh-close
        const res = await api.github.postClose(topic.id, payload.body, "completed");
        message = res.message;
      }
      setPersisted((prev) => [...prev, message]);
      setPendingCommand(null);
      onTopicUpdated();
    } catch (err) {
      setPendingCommand((prev) =>
        prev?.kind === kind
          ? { ...prev, posting: false, error: (err as Error).message }
          : prev,
      );
    }
  }

  return (
    <div className="h-full flex min-h-0">
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {reminders.reminder && reminders.reminder.status === "fired" && (
          <ReminderBanner
            reminder={reminders.reminder}
            busy={reminders.reminderBusy}
            onDone={() => void reminders.runReminderClear(true)}
          />
        )}
        <TopicSummaryPanel
          key={topic.id}
          topicId={topic.id}
          summary={summary.summary}
          busy={summary.busy}
          error={summary.error}
          refreshNotice={summary.refreshNotice}
          onSave={summary.save}
          onResolve={summary.resolve}
          onRemove={summary.remove}
          onRefresh={() => void summary.refresh()}
          onToggleVisible={() => void summary.toggleVisible()}
          onDismissError={summary.clearError}
        />
        <div ref={conv.scrollRef} onScroll={conv.onScroll} className="flex-1 overflow-y-auto p-4 min-w-0">
          <div
            className="relative mx-auto space-y-3"
            style={{ maxWidth: chatWidth }}
          >
            <ResizeHandle onMouseDown={onChatResize} />
          {conv.loadingOlder && (
            <div className="text-center text-[11px] text-muted py-1">
              Loading earlier messages…
            </div>
          )}
          {visibleMessages.length === 0 && !streaming && (
            <div className="text-sm text-muted text-center pt-8">
              Send a message to start the conversation.
            </div>
          )}
          {(() => {
            // In a scheduled topic the user turn is the repeated automation
            // prompt — collapse it so generated content gets the room.
            const renderMessage = (m: (typeof visibleMessages)[number], grouped: boolean) => (
              <TranscriptMessage
                key={m.id}
                message={m}
                streaming={streaming}
                retryable={m.id === conv.retryableId}
                onRetry={conv.retryTurn}
                onDelete={conv.deletion.requestDeleteMessage}
                collapsible={m.role === "user" && topic.schedule != null}
                hideAgentBadge={grouped}
              />
            );

            // Wrap consecutive agent-tagged turns (prompt + answer) in a dashed
            // purple frame with a single AGENT badge, so an agent exchange reads
            // as one block instead of two separately-tagged bubbles.
            const out: ReactNode[] = [];
            let i = 0;
            while (i < visibleMessages.length) {
              const m = visibleMessages[i];
              const aid = m.agent_session_id;
              if (aid != null) {
                const group: typeof visibleMessages = [];
                let j = i;
                while (j < visibleMessages.length && visibleMessages[j].agent_session_id === aid) {
                  group.push(visibleMessages[j]);
                  j++;
                }
                // Prefer the agent's public UUID for the follow-up command;
                // fall back to the integer id for legacy rows without one.
                const agentRef = m.agent_session_public_id ?? String(aid);
                out.push(
                  <div
                    key={`agent-${aid}-${m.id}`}
                    className="space-y-3 rounded-lg border border-dashed border-purple-500/50 bg-purple-500/[0.03] p-2.5"
                  >
                    <AgentExchangeBadge agentSessionId={aid} />
                    {group.map((gm) => renderMessage(gm, true))}
                    {agentsEnabled && (
                      <div className="flex justify-end">
                        <button
                          type="button"
                          onClick={() => prefillAgentFollowUp(agentRef)}
                          className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-purple-500/40 px-2 py-0.5 text-[10px] font-medium text-purple-600 hover:bg-purple-500/10 dark:text-purple-300"
                          data-tooltip="Continue this agent session"
                        >
                          <ArrowRightCircle size={11} />
                          Continue session
                        </button>
                      </div>
                    )}
                  </div>,
                );
                i = j;
              } else {
                out.push(renderMessage(m, false));
                i++;
              }
            }
            return out;
          })()}
          <TranscriptTail
            visibleMessages={visibleMessages}
            streaming={streaming}
            pendingContent={conv.pendingContent}
            onPickSuggestion={conv.sendSuggestion}
            onStop={conv.stop}
          />
        </div>
      </div>

      <div className="border-t border-border p-3 pb-safe">
        <div className="mx-auto space-y-2" style={{ maxWidth: chatWidth }}>
          <UndoDeleteToasts deletion={conv.deletion} />
          <ConversationNotes
            notes={conv.notes}
            container="topic"
            containerId={topic.id}
            title={topic.title}
            hasIssue={issueAssociationsEnabled && topic.github_issue_number !== null}
            allowPostComment
          />
          {pendingCommand && (
            <CommandDraftCard
              title={cardTitle(pendingCommand)}
              subtitle={cardSubtitle(pendingCommand)}
              initialBody={pendingCommand.body}
              initialTitle={
                pendingCommand.kind === "gh-create" ? pendingCommand.title ?? "" : undefined
              }
              titleLabel="Issue title"
              bodyPlaceholder={cardBodyPlaceholder(pendingCommand.kind)}
              bodyRequired={pendingCommand.kind !== "gh-close"}
              loading={pendingCommand.loading}
              posting={pendingCommand.posting}
              error={pendingCommand.error}
              sendLabel={cardSendLabel(pendingCommand.kind)}
              postingLabel={cardPostingLabel(pendingCommand.kind)}
              confirmHint={cardConfirmHint(pendingCommand.kind)}
              onSend={postCommandDraft}
              onCancel={() => setPendingCommand(null)}
              onPopOut={
                pendingCommand.loading
                  ? undefined
                  : ({ body, title }) => {
                      const kind = pendingCommand.kind;
                      detachedDraftStore.open({
                        kind,
                        container: "topic",
                        containerId: topic.id,
                        title: cardTitle(pendingCommand),
                        subtitle: cardSubtitle(pendingCommand),
                        initialText: body,
                        initialTitle: title,
                        titleLabel: "Issue title",
                        bodyPlaceholder: cardBodyPlaceholder(kind),
                        bodyRequired: kind !== "gh-close",
                        sendLabel: cardSendLabel(kind),
                        postingLabel: cardPostingLabel(kind),
                        confirmHint: cardConfirmHint(kind),
                      });
                      setPendingCommand(null);
                    }
              }
            />
          )}
          <Composer
            value={composer.draft}
            onChange={composer.setDraft}
            onSend={() => void conv.send()}
            onStop={conv.stop}
            streaming={streaming}
            suggestions={composer.suggestions}
            userHistory={conv.userHistory}
            speech={composer.speech}
            interimText={composer.interimText}
            height={composerHeight}
            onResizeStart={onComposerResize}
            focusToken={composerFocusToken}
            toolbarStart={
              <>
                <ComposerModelControls />
                <RoleSelector
                  value={topic.role_id ?? null}
                  onChange={(roleId) => void onSetRole?.(roleId)}
                  open={conv.roleOpen}
                  onOpenChange={conv.setRoleOpen}
                />
              </>
            }
            attachments={conv.attachments}
          />
        </div>
      </div>
      </div>
      {showStats && <ChatStatsPanel streamKey={streamKey} messages={conv.messages} />}
      {reminders.reminderModal && (
        <ReminderModal
          container="topic"
          containerId={topic.id}
          existing={reminders.reminder}
          initialNote={reminders.reminderModal.note}
          onClose={() => reminders.setReminderModal(null)}
          onSaved={(saved) => {
            reminders.setReminderModal(null);
            reminders.handleReminderSaved(saved);
          }}
        />
      )}
      <NotesConfirmModal notes={conv.notes} />
    </div>
  );
}
