import { useEffect, useMemo, useState } from "react";
import { MessageSquarePlus, MessagesSquare } from "lucide-react";
import type { SlashCommand } from "../lib/commands";
import { useSettings } from "../lib/settingsStore";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { useResizableHeight } from "../lib/useResizableHeight";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
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
  onCreated,
}: {
  tree: TopicNode[];
  initialParentId?: number | null;
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
        onCreated={onCreated}
      />
    </div>
  );
}

/**
 * Landing surface shown in the Chats pane when nothing is selected. Lets the
 * user type a prompt that spins up a fresh chat and sends the first message,
 * matching the agent tab's start-task experience.
 */
export function ChatStartHero({
  onStart,
}: {
  onStart: (prompt: string) => void | Promise<void>;
}) {
  const settings = useSettings();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const { height: composerHeight, onMouseDown: onComposerResize } =
    useResizableHeight({
      storageKey: "precursor:chat-start-composer:height",
      defaultHeight: 56,
      min: 40,
      max: 480,
    });
  const [interimText, setInterimText] = useState("");
  const speech = useAzureSpeech({
    onFinalChunk: (text) => {
      const chunk = text.trim();
      if (!chunk) return;
      setPrompt((d) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk));
      setInterimText("");
    },
    onInterim: setInterimText,
    enabled: settings?.stt_azure_ready ?? false,
    lang: settings?.azure_speech_language || undefined,
  });
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

  const suggestions = useMemo<SlashCommand[]>(() => [], []);

  async function start(): Promise<void> {
    const text = prompt.trim();
    if (!text || busy) return;
    setBusy(true);
    if (speech.listening) speech.stop();
    try {
      await onStart(text);
      setPrompt("");
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
        Chats are quick, throwaway conversations. Type a prompt to spin one up
        and send your first message.
      </p>
      <Composer
        value={prompt}
        onChange={setPrompt}
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
        toolbarStart={<ComposerModelControls />}
      />
    </div>
  );
}
