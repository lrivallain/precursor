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
