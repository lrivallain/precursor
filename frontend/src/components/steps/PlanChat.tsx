import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, apiErrorMessage } from "../../lib/api";
import type { Chat } from "../../lib/types";
import { ChatSessionPanel } from "../ChatSessionPanel";
import { ArtifactActionContext, type ArtifactAction } from "./ArtifactCard";
import { BTN_SECONDARY, ErrorBanner } from "./StepUi";

/**
 * A plan chat (a step's discussion, or the consolidation chat): a normal Chat
 * rendered by the same panel as the Chats section. `open` creates the chat on
 * first use, so nothing exists server-side until the tab is actually opened.
 */
export function PlanChat({
  open,
  onOpened,
  artifactAction,
}: {
  open: () => Promise<Chat>;
  /** The chat was just opened (it may have moved the step to "in progress"). */
  onOpened?: () => void;
  /** What the "send" button of a deliverable the model marked does. */
  artifactAction?: ArtifactAction;
}) {
  const [chat, setChat] = useState<Chat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setChat(null);
    setError(null);
    open()
      .then((c) => {
        if (cancelled) return;
        setChat(c);
        onOpened?.();
      })
      .catch((err) => {
        if (!cancelled) setError(apiErrorMessage(err, "Could not open the discussion."));
      });
    return () => {
      cancelled = true;
    };
    // `open` and `onOpened` are fresh closures every render; the chat is
    // identified by the key the parent mounts this with.
  }, [attempt]);

  const refresh = useCallback(async () => {
    if (!chat) return;
    try {
      setChat(await api.chats.get(chat.id));
    } catch {
      // the chat may be gone; the next open surfaces the error
    }
  }, [chat]);

  if (error) {
    return (
      <div className="space-y-3 p-3">
        <ErrorBanner message={error} />
        <button type="button" className={BTN_SECONDARY} onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </div>
    );
  }
  if (!chat) {
    return (
      <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted" role="status">
        <Loader2 size={16} className="animate-spin" /> Opening the discussion…
      </div>
    );
  }
  return (
    <ArtifactActionContext.Provider value={artifactAction ?? null}>
    <ChatSessionPanel
      key={chat.id}
      chat={chat}
      onChatUpdated={() => void refresh()}
      onArchived={() => setAttempt((n) => n + 1)}
      onSetRole={async (role) => {
        await api.chats.update(chat.id, { role });
        await refresh();
      }}
    />
    </ArtifactActionContext.Provider>
  );
}
