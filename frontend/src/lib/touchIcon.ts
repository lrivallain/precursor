// The home-screen icon behind the remote-access tunnel.
//
// iOS fetches `apple-touch-icon` outside the page, without the private relay's
// sign-in cookie, so the relay answers with its login redirect and iOS falls
// back to a letter on a plain background. The page itself holds the cookie:
// it loads the icon and hands iOS a data: URL, which needs no request.

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Only a page reached through a tunnel or proxy needs the icon inlined. */
export function needsInlineIcon(hostname: string): boolean {
  return !LOCAL_HOSTS.has(hostname);
}

export async function inlineTouchIcon(): Promise<void> {
  if (!needsInlineIcon(window.location.hostname)) return;
  const link = document.querySelector<HTMLLinkElement>('link[rel="apple-touch-icon"]');
  if (!link) return;
  try {
    const response = await fetch(link.href, { credentials: "same-origin" });
    if (!response.ok) return;
    const blob = await response.blob();
    link.href = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  } catch {
    // Best effort: without it, iOS just shows its default icon.
  }
}
