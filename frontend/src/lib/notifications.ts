/**
 * Browser-notification helpers (issue #22).
 *
 * Thin wrapper over the Notification API: request permission, and fire a
 * notification only when it's actually useful — permission granted and the
 * Precursor window is not focused (so we never interrupt someone who's already
 * looking at the app). All calls are no-ops where the API is unavailable.
 */

function supported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function notificationsSupported(): boolean {
  return supported();
}

export function notificationPermission(): NotificationPermission {
  return supported() ? Notification.permission : "denied";
}

/** Prompt for permission; resolves to the resulting permission state. */
export async function requestNotificationPermission(): Promise<NotificationPermission> {
  if (!supported()) return "denied";
  if (Notification.permission !== "default") return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch {
    return Notification.permission;
  }
}

interface NotifyOptions {
  title: string;
  body?: string;
  /** Coalesces repeat notifications for the same topic (renotify replaces). */
  tag?: string;
}

/**
 * Show a notification, but only when permission is granted and the window is
 * NOT focused. Clicking it focuses the window. Returns true if shown.
 */
export function notifyIfUnfocused({ title, body, tag }: NotifyOptions): boolean {
  if (!supported() || Notification.permission !== "granted") return false;
  // Don't notify when the user is already looking at Precursor.
  if (typeof document !== "undefined" && document.hasFocus()) return false;
  try {
    const n = new Notification(title, { body, tag });
    n.onclick = () => {
      window.focus();
      n.close();
    };
    return true;
  } catch {
    return false;
  }
}

interface NotifyNowOptions extends NotifyOptions {
  /**
   * Invoked when the user clicks the notification (after the window is
   * focused). Use it to deep-link — e.g. select the waiting agent.
   */
  onClick?: () => void;
  /**
   * Keep the notification on screen until the user acts on it. Suited to
   * out-of-band "an agent is blocked waiting for you" prompts.
   */
  requireInteraction?: boolean;
}

/**
 * Show a notification regardless of window focus — the out-of-band signal for
 * events that need the human even when they're looking at another part of the
 * app (idea 5: "Claude is waiting for you"). Clicking focuses the window and
 * runs `onClick` so we can jump straight to the blocked agent. Returns true if
 * shown.
 */
export function notifyNow({
  title,
  body,
  tag,
  onClick,
  requireInteraction,
}: NotifyNowOptions): boolean {
  if (!supported() || Notification.permission !== "granted") return false;
  try {
    const n = new Notification(title, { body, tag, requireInteraction });
    n.onclick = () => {
      window.focus();
      onClick?.();
      n.close();
    };
    return true;
  } catch {
    return false;
  }
}
