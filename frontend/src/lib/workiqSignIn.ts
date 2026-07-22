/**
 * Interactive WorkIQ sign-in driven from the SPA.
 *
 * The hosted WorkIQ endpoint is OAuth-protected. Re-authenticating pops a
 * browser for the authorization-code grant, then a loopback callback page tries
 * to close itself once auth completes. Browsers only let a script close a window
 * a *script* opened — a tab opened out-of-band by the backend's
 * ``webbrowser.open`` can't be closed and lingers. So we open the sign-in in a
 * script-opened popup here: opened synchronously inside the click (to survive
 * popup blockers) at ``about:blank``, then navigated to the authorization URL
 * the backend surfaces over the ``/api/events`` SSE bus. The callback page can
 * then close that popup for real.
 *
 * A second, hands-free path re-authenticates with *zero* clicks: when the
 * browser still holds a live Entra SSO session the backend's silent
 * ``prompt=none`` pass completes without any UI, so we can drive its
 * authorization URL through an **invisible iframe** instead of a popup — no
 * gesture, no visible window. Only when a silent pass genuinely needs
 * interaction (or framing/cookies block it) do we fall back to the popup flow.
 */

import { api } from "./api";
import type { MCPServerStatus } from "./types";

const POPUP_WIDTH = 520;
const POPUP_HEIGHT = 680;

// How long to leave the invisible iframe attached after the silent attempt
// resolves, so a just-completed loopback redirect can finish loading before we
// tear the frame down.
const SILENT_FRAME_CLEANUP_MS = 1000;

// How often to check whether the sign-in popup has been closed.
const ABANDON_POLL_MS = 500;
// Grace period after the popup closes before we treat the sign-in as abandoned.
// On success the loopback callback page auto-closes the popup a couple of
// seconds after the redirect, so we wait a touch longer than that for the fetch
// to resolve on its own before proactively cancelling.
const ABANDON_GRACE_MS = 3000;

// The popup awaiting the authorization URL for the in-flight sign-in, if any.
let pendingPopup: Window | null = null;
// Set once we've navigated the popup, so a later failure doesn't close a window
// that's mid-sign-in.
let pendingNavigated = false;
// The invisible iframe awaiting the authorization URL for an in-flight silent
// (hands-free) re-auth, if any.
let pendingFrame: HTMLIFrameElement | null = null;

/**
 * Feed the authorization URL (delivered over SSE) to the waiting sign-in target.
 *
 * A no-op when this window started neither a popup nor a silent frame — only the
 * window that kicked off the sign-in has one, even though the event is broadcast
 * to all of them. The popup (interactive) wins over the frame if somehow both
 * exist.
 *
 * This may fire more than once for a single sign-in: the backend first tries a
 * silent ``prompt=none`` pass and, if Entra needs interaction, re-surfaces the
 * interactive URL — we simply re-navigate the same target to whichever URL comes.
 */
export function emitWorkiqAuthUrl(url: string): void {
  if (pendingPopup && !pendingPopup.closed) {
    pendingPopup.location.href = url;
    pendingNavigated = true;
    return;
  }
  if (pendingFrame) {
    pendingFrame.src = url;
  }
}

function openSigninPopup(): Window | null {
  const width = Math.min(POPUP_WIDTH, window.screen.availWidth);
  const height = Math.min(POPUP_HEIGHT, window.screen.availHeight);
  const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
  const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
  const features = `popup=yes,width=${width},height=${height},left=${left},top=${top}`;
  const win = window.open("about:blank", "workiq-signin", features);
  if (win) {
    // A brief placeholder so the popup isn't a jarring blank while the backend
    // spins up the flow and hands back the authorization URL.
    win.document.title = "WorkIQ sign-in…";
    win.document.body.style.cssText =
      "margin:0;display:flex;align-items:center;justify-content:center;" +
      "height:100vh;font-family:system-ui,-apple-system,sans-serif;color:#6b7280;" +
      "background:#ffffff";
    win.document.body.textContent = "Preparing WorkIQ sign-in…";
  }
  return win;
}

/**
 * Run the interactive WorkIQ sign-in, preferring a script-opened popup.
 *
 * MUST be called directly from a user gesture (click) with no ``await`` before
 * it, so the synchronous ``window.open`` isn't treated as a blocked popup.
 * Resolves with the refreshed server status; the popup self-closes on success.
 *
 * Returns ``null`` when the user abandons the sign-in by closing the popup
 * before it completes: we proactively tell the backend to cancel (so it frees
 * the fixed OAuth loopback port at once instead of squatting it until the
 * callback times out) and resolve ``null`` so the caller can quietly drop its
 * "Signing in…" state without surfacing an error.
 */
export async function signInWorkiq(): Promise<MCPServerStatus | null> {
  const popup = openSigninPopup();
  pendingPopup = popup;
  pendingNavigated = false;
  const usePopup = popup !== null;
  let settled = false;
  const stopWatch = watchForAbandon(popup, () => settled);
  try {
    return await api.mcp.reauthenticateWorkiq({ usePopup });
  } catch (err) {
    // The user closed the popup and we cancelled the sign-in — a quiet
    // abandonment, not a failure to surface.
    if (abandoned) return null;
    // The flow failed before we ever navigated the popup — tear down the blank
    // throwaway window rather than strand it. If it was already navigated the
    // user may be mid-sign-in, so leave it be.
    if (popup && !pendingNavigated && !popup.closed) popup.close();
    throw err;
  } finally {
    settled = true;
    stopWatch();
    if (pendingPopup === popup) pendingPopup = null;
  }
}

// Whether the last (or in-flight) interactive sign-in was cancelled by the user
// closing its popup. Read in ``signInWorkiq``'s catch to distinguish a quiet
// abandonment from a real error.
let abandoned = false;

/**
 * Watch a sign-in popup and, if the user closes it before the flow finishes,
 * ask the backend to cancel so it releases the fixed loopback port immediately.
 *
 * Only fires once the popup has been closed *and* the sign-in still hasn't
 * settled after a short grace — long enough that the success path (whose
 * callback page auto-closes the popup a beat after the redirect) resolves on its
 * own first and is never mistaken for an abandonment. A no-op when no popup was
 * opened. Returns a stopper to call once the flow resolves.
 */
function watchForAbandon(popup: Window | null, isSettled: () => boolean): () => void {
  abandoned = false;
  if (!popup) return () => {};
  let graceTimer: number | undefined;
  const poll = window.setInterval(() => {
    if (isSettled()) {
      window.clearInterval(poll);
      return;
    }
    if (popup.closed && graceTimer === undefined) {
      graceTimer = window.setTimeout(() => {
        if (isSettled()) return;
        abandoned = true;
        // Best-effort: the backend releases the loopback port and the pending
        // sign-in request then rejects (409), which the catch treats as a quiet
        // abandonment.
        void api.mcp.cancelReauthenticateWorkiq().catch(() => {});
      }, ABANDON_GRACE_MS);
    }
  }, ABANDON_POLL_MS);
  return () => {
    window.clearInterval(poll);
    if (graceTimer !== undefined) window.clearTimeout(graceTimer);
  };
}

function openSilentFrame(): HTMLIFrameElement {
  const frame = document.createElement("iframe");
  frame.title = "WorkIQ silent sign-in";
  frame.setAttribute("aria-hidden", "true");
  // Fully off-screen and inert so the silent Entra round-trip is invisible.
  frame.style.cssText =
    "position:fixed;width:0;height:0;border:0;left:-9999px;top:-9999px;visibility:hidden;";
  document.body.appendChild(frame);
  return frame;
}

/**
 * Attempt the hands-free silent WorkIQ re-auth with no user gesture.
 *
 * Drives the backend's ``prompt=none`` pass through an invisible iframe: if the
 * browser still holds a live Entra SSO session it completes with zero clicks and
 * we resolve ``true`` (the caller can drop the sign-in banner). When a silent
 * pass can't complete — Entra reports interaction is required, the auto re-auth
 * setting is off, or framing/cookies block it — the backend answers with
 * ``interaction_required`` and we resolve ``false`` so the caller keeps the
 * manual banner. Never throws for an ordinary "needs a human" outcome.
 *
 * At most one silent attempt runs at a time (a second call is a no-op that
 * resolves ``false``).
 */
export async function signInWorkiqSilent(): Promise<boolean> {
  if (pendingFrame) return false;
  const frame = openSilentFrame();
  pendingFrame = frame;
  try {
    const status = await api.mcp.reauthenticateWorkiq({ silentOnly: true });
    return status.interaction_required !== true;
  } catch {
    // Any failure (409 in-progress, transport, 502) just means "fall back to the
    // manual banner" — never surface an error for a background attempt.
    return false;
  } finally {
    if (pendingFrame === frame) pendingFrame = null;
    window.setTimeout(() => frame.remove(), SILENT_FRAME_CLEANUP_MS);
  }
}
