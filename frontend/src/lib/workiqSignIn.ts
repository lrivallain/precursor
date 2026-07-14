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
 */

import { api } from "./api";
import type { MCPServerStatus } from "./types";

const POPUP_WIDTH = 520;
const POPUP_HEIGHT = 680;

// The popup awaiting the authorization URL for the in-flight sign-in, if any.
let pendingPopup: Window | null = null;
// Set once we've navigated the popup, so a later failure doesn't close a window
// that's mid-sign-in.
let pendingNavigated = false;

/**
 * Feed the authorization URL (delivered over SSE) to the waiting popup.
 *
 * A no-op when this window has no pending popup — only the window that started
 * the sign-in opened one, even though the event is broadcast to all of them.
 */
export function emitWorkiqAuthUrl(url: string): void {
  if (pendingPopup && !pendingPopup.closed) {
    pendingPopup.location.href = url;
    pendingNavigated = true;
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
 */
export async function signInWorkiq(): Promise<MCPServerStatus> {
  const popup = openSigninPopup();
  pendingPopup = popup;
  pendingNavigated = false;
  const usePopup = popup !== null;
  try {
    return await api.reauthenticateWorkiq({ usePopup });
  } catch (err) {
    // The flow failed before we ever navigated the popup — tear down the blank
    // throwaway window rather than strand it. If it was already navigated the
    // user may be mid-sign-in, so leave it be.
    if (popup && !pendingNavigated && !popup.closed) popup.close();
    throw err;
  } finally {
    if (pendingPopup === popup) pendingPopup = null;
  }
}
