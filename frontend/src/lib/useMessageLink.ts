import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { RefObject } from "react";
import { parseAppRoute } from "./routes";

function subscribe(callback: () => void): () => void {
  const events = ["popstate", "hashchange", "precursor:navigate"];
  for (const event of events) window.addEventListener(event, callback);
  return () => {
    for (const event of events) window.removeEventListener(event, callback);
  };
}

export function useDiscussionTarget(
  kind: "topic" | "chat" | "agent",
  publicId: string | null | undefined,
  slug?: string,
): string | null {
  const href = useSyncExternalStore(subscribe, () => window.location.href);
  const route = parseAppRoute();
  const matches = kind === "topic"
    ? route.mode === "topics" && (route.topicPublicId === publicId ||
        (slug != null && route.topicPath.at(-1) === slug))
    : kind === "chat"
      ? route.mode === "chats" && (route.chatPublicId === publicId || (slug != null && route.chatSlug === slug))
      : route.mode === "agents" && route.agentRef === publicId;
  if (!publicId || !matches) return null;
  return new URLSearchParams(new URL(href).hash.slice(1)).get(kind === "agent" ? "event" : "message");
}

export function useMessageJump({
  target,
  ready,
  resetKey,
  prepare,
  scrollRef,
  unpin,
}: {
  target: string | null;
  ready: boolean;
  resetKey: string;
  prepare: (target: string, signal: AbortSignal) => Promise<boolean>;
  scrollRef: RefObject<HTMLDivElement | null>;
  unpin: () => void;
}): string | null {
  const [error, setError] = useState<{ target: string; key: string; message: string } | null>(null);
  const callbacks = useRef({ prepare, unpin });
  callbacks.current = { prepare, unpin };
  useEffect(() => {
    if (!target || !ready) return;
    const abort = new AbortController();
    void (async () => {
      try {
        callbacks.current.unpin();
        if (!(await callbacks.current.prepare(target, abort.signal))) {
          if (!abort.signal.aborted) {
            setError({ target, key: resetKey, message: "This message is no longer available." });
          }
          return;
        }
        // Loading older pages, unfolding work and switching panes all commit
        // asynchronously. Scope the lookup to this discussion, not the document.
        for (let frame = 0; frame < 60 && !abort.signal.aborted; frame++) {
          await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
          if (abort.signal.aborted) return;
          const element = scrollRef.current?.querySelector<HTMLElement>(
            `[data-message-link="${CSS.escape(target)}"]`,
          );
          if (!element) continue;
          callbacks.current.unpin();
          element.scrollIntoView({ block: "center" });
          element.focus({ preventScroll: true });
          if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            element.animate(
              [{ boxShadow: "0 0 0 2px var(--accent)" }, { boxShadow: "0 0 0 2px transparent" }],
              { duration: 1600, easing: "ease-out" },
            );
          }
          setError(null);
          return;
        }
        if (!abort.signal.aborted) {
          setError({ target, key: resetKey, message: "This message could not be displayed." });
        }
      } catch (reason) {
        if (!abort.signal.aborted) {
          setError({
            target,
            key: resetKey,
            message: `Could not open this message: ${reason instanceof Error ? reason.message : String(reason)}`,
          });
        }
      }
    })();
    return () => abort.abort();
  }, [target, ready, resetKey, scrollRef]);
  return error?.target === target && error.key === resetKey ? error.message : null;
}
