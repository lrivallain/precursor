// Links to this app's own pages, as tools write them: absolute, on the
// computer's loopback address (`http://127.0.0.1:8000/topics/...`), because the
// same text also goes to external MCP clients. In the app they work better as
// paths: same page on the computer, and the only form that resolves on a phone
// that reached Precursor through the remote-access tunnel.

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const APP_ROUTE = /^\/(?:topics|chats|agents|live|steps|ws|workflows)(?:\/|$)/;

function portOf(url: URL): string {
  return url.port || (url.protocol === "https:" ? "443" : "80");
}

/**
 * The app-relative form of a link to one of this app's pages, else `href`.
 *
 * Only for links on the app's own loopback origin (`appOrigin`, from the
 * backend): another local service, or another Precursor on a different port,
 * keeps its absolute link. Nothing is rewritten until that origin is known.
 */
export function localizeAppHref(
  href: string | undefined,
  appOrigin: string | null,
): string | undefined {
  if (!href || !appOrigin || !/^https?:\/\//i.test(href)) return href;
  let url: URL;
  let app: URL;
  try {
    url = new URL(href);
    app = new URL(appOrigin);
  } catch {
    return href;
  }
  if (!LOOPBACK_HOSTS.has(url.hostname) || !APP_ROUTE.test(url.pathname)) return href;
  if (portOf(url) !== portOf(app)) return href;
  return url.pathname + url.search + url.hash;
}
