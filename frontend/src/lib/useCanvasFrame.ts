import { useEffect, useRef, type RefObject } from "react";
import { call } from "./apiClient";
import {
  CANVAS_WORKSPACE_SLUG,
  fillComposer,
  overflowOverride,
  readCanvasMessage,
  type CanvasRef,
} from "./canvas";
import type { WsDiscussionRef } from "./routes";
import { openWorkspaceFile } from "./workspaceLink";

// How long to wait for the discussion panel's message box to mount.
const COMPOSER_WAIT_MS = 4000;
const COMPOSER_POLL_MS = 100;

async function conversationOf(conversationId: string): Promise<WsDiscussionRef | null> {
  try {
    const chat = await call("GET /api/chats/by-public-id/{public_id}", {
      path: { public_id: conversationId },
    });
    return { kind: "chat", ref: chat.slug };
  } catch {
    // Not a chat: a topic, or a conversation that's gone.
  }
  try {
    await call("GET /api/topics/by-public-id/{public_id}", { path: { public_id: conversationId } });
    return { kind: "topic", ref: conversationId };
  } catch {
    return null;
  }
}

function fillWhenReady(text: string): void {
  const started = Date.now();
  const tick = () => {
    if (fillComposer(text)) return;
    if (Date.now() - started < COMPOSER_WAIT_MS) window.setTimeout(tick, COMPOSER_POLL_MS);
  };
  tick();
}

/**
 * Hand a canvas's question to its conversation: the message box beside the
 * canvas when there is one (the conversation itself, or a workspace
 * discussion), else the canvas opened in the Files section with its
 * conversation as the discussion. The text is never sent for the user.
 */
export async function askFromCanvas(
  canvas: CanvasRef,
  text: string,
  frame: Element | null,
): Promise<void> {
  if (fillComposer(text, frame)) return;
  const discussion = await conversationOf(canvas.conversationId);
  if (!discussion) return;
  openWorkspaceFile(CANVAS_WORKSPACE_SLUG, canvas.path, discussion);
  fillWhenReady(text);
}

/** Listen to what a canvas page posts through `canvas-sdk.js` (resize, ask). */
export function useCanvasFrame(
  frame: RefObject<HTMLIFrameElement | null>,
  canvas: CanvasRef | null,
  onResize?: (height: number) => void,
): void {
  // Callers re-parse the canvas on every render; listen once per frame.
  const latest = useRef({ canvas, onResize });
  latest.current = { canvas, onResize };
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const { canvas: current, onResize: resize } = latest.current;
      if (!current || event.source !== frame.current?.contentWindow) return;
      const msg = readCanvasMessage(event.data);
      if (msg?.type === "precursor:canvas-resize") resize?.(msg.height);
      if (msg?.type === "precursor:canvas-ask") void askFromCanvas(current, msg.text, frame.current);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [frame]);
}

/**
 * Hide a canvas page's own vertical scrollbar while its frame is sized to the
 * content. Otherwise a frame a few pixels short shows the scrollbar, the page
 * rewraps narrower and taller, the frame grows, the scrollbar goes, the page
 * rewraps shorter, and the frame flickers between the two heights. The page's
 * own `overflow-y` is put back when the override ends.
 */
export function useFrameScrollbarHidden(
  frame: RefObject<HTMLIFrameElement | null>,
  hidden: boolean,
): void {
  useEffect(() => {
    const el = frame.current;
    if (!hidden || !el) return;
    const override = overflowOverride();
    const apply = () => {
      const style = el.contentDocument?.documentElement.style;
      if (style) override.apply(style);
    };
    apply();
    el.addEventListener("load", apply);
    return () => {
      el.removeEventListener("load", apply);
      override.restore();
    };
  }, [frame, hidden]);
}
