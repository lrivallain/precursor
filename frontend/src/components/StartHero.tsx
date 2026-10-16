import { useState } from "react";
import { MessageSquarePlus, MessagesSquare } from "lucide-react";
import { SLASH_COMMANDS } from "../lib/commands";
import { resolveSkillPrompt } from "../lib/skillPrompt";
import type { StartChatOptions } from "../lib/useChatsController";
import { useComposerInput } from "../lib/useComposerInput";
import { useStartChatDraft } from "../lib/useStartChatDraft";
import { useResizableHeight } from "../lib/useResizableHeight";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { RoleSelector } from "./RoleSelector";
import { TopicCreateForm } from "./TopicCreateForm";
import type { Topic, TopicNode } from "../lib/types";

/**
 * Landing surface shown in the Topics pane (and the home launcher) when no topic
 * is selected. Renders the topic create form inline — the same form the modal
 * uses — so creating a topic matches the chat/live/agent start experiences.
 */
export function TopicStartHero({
  tree,
  initialParentId = null,
  collectionId = null,
  onCreated,
}: {
  tree: TopicNode[];
  initialParentId?: number | null;
  collectionId?: number | null;
  onCreated: (topic: Topic) => void;
}) {
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center gap-3 p-8">
      <div className="flex items-center gap-2">
        <MessagesSquare size={18} />
        <h2 className="text-sm font-medium">Start a new topic</h2>
      </div>
      <p className="text-[12px] text-muted">
        Topics are long-lived threads that keep their own history, context, and
        optional linked issue. Fill in a title to start a focused conversation.
      </p>
      <TopicCreateForm
        tree={tree}
        initialParentId={initialParentId}
        collectionId={collectionId}
        onCreated={onCreated}
      />
    </div>
  );
}

// Built-in commands act on an open conversation, so the start composer only
// offers skills.
const BUILTIN_COMMAND_NAMES: ReadonlySet<string> = new Set(SLASH_COMMANDS.map((c) => c.name));

/**
 * Landing surface shown in the Chats pane when nothing is selected. Lets the
 * user type a prompt (with `/skills` and attached files) that spins up a fresh
 * chat and sends the first message, matching the agent tab's start-task
 * experience.
 */
export function ChatStartHero({
  onStart,
}: {
  onStart: (
    prompt: string,
    roleName: string | null,
    opts?: StartChatOptions,
  ) => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  // Assistant Role the new chat is created with. Null = the default role.
  const [roleName, setRoleName] = useState<string | null>(null);
  const [roleOpen, setRoleOpen] = useState(false);
  const { height: composerHeight, onMouseDown: onComposerResize } =
    useResizableHeight({
      storageKey: "precursor:chat-start-composer:height",
      defaultHeight: 56,
      min: 40,
      max: 480,
    });
  const { draft, setDraft, interimText, speech, suggestions, parseCommand } =
    useComposerInput({ surface: "chat", exclude: BUILTIN_COMMAND_NAMES });
  const startDraft = useStartChatDraft(roleName);
  const { attachments } = startDraft;

  async function start(): Promise<void> {
    const text = draft.trim();
    const hasAttachments = attachments.pending.length > 0;
    if ((!text && !hasAttachments) || busy || attachments.uploadingCount > 0) return;
    setBusy(true);
    if (speech.listening) speech.stop();
    const resolved = resolveSkillPrompt(text, parseCommand);
    // The send unmounts this screen; keep the draft chat through that.
    startDraft.release();
    try {
      await onStart(resolved.content, roleName, {
        chat: startDraft.chat,
        attachments: attachments.pending,
        promptOverride: resolved.promptOverride,
      });
      setDraft("");
      startDraft.reset();
    } catch (err) {
      startDraft.reclaim();
      throw err;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center gap-3 p-8">
      <div className="flex items-center gap-2">
        <MessageSquarePlus size={18} />
        <h2 className="text-sm font-medium">Start a new chat</h2>
      </div>
      <p className="text-[12px] text-muted">
        Chats are quick, throwaway conversations. Type a prompt, use a{" "}
        <code>/skill</code> or attach files, and your first message is sent.
      </p>
      <Composer
        value={draft}
        onChange={setDraft}
        onSend={() => void start()}
        onStop={() => {}}
        streaming={false}
        suggestions={suggestions}
        userHistory={[]}
        speech={speech}
        interimText={interimText}
        height={composerHeight}
        onResizeStart={onComposerResize}
        disabled={busy}
        autoFocus
        placeholder="e.g. Summarize the tradeoffs between REST and GraphQL…"
        attachments={attachments}
        toolbarStart={
          <>
            <ComposerModelControls />
            <RoleSelector
              value={roleName}
              onChange={setRoleName}
              open={roleOpen}
              onOpenChange={setRoleOpen}
              disabled={busy}
            />
          </>
        }
      />
    </div>
  );
}
