// Links to this app's own pages, as tools write them: absolute, on the
// computer's loopback address (`http://127.0.0.1:8000/topics/...`), because the
// same text also goes to external MCP clients. In the app they work better as
// paths: same page on the computer, and the only form that resolves on a phone
// that reached Precursor through the remote-access tunnel.

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const APP_ROUTE = /^\/(?:topics|chats|agents|live|steps|ws|workflows)(?:\/|$)/;

/** The app-relative form of a loopback link to one of the app's pages, else `href`. */
export function localizeAppHref(href: string | undefined): string | undefined {
  if (!href || !/^https?:\/\//i.test(href)) return href;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return href;
  }
  if (!LOOPBACK_HOSTS.has(url.hostname) || !APP_ROUTE.test(url.pathname)) return href;
  return url.pathname + url.search + url.hash;
}
