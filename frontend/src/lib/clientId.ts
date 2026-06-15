// Per-window identifier used to suppress echoes from cross-window event
// notifications. Stored in sessionStorage so it survives reloads of the
// same window but is unique per tab.
const KEY = "precursor:client_id";

function pick(): string {
  try {
    const existing = sessionStorage.getItem(KEY);
    if (existing) return existing;
    const fresh =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem(KEY, fresh);
    return fresh;
  } catch {
    return Math.random().toString(36).slice(2);
  }
}

export const CLIENT_ID = pick();
