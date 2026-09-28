// Tells views that show where agents and workflows are declared (the migration
// banners on the Agents and Workflows homes) that it may have changed: the
// wizard migrated, switched back, cleaned up, or wrote missing files.

const EVENT = "precursor:definitions-changed";

export function notifyDefinitionsChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

export function subscribeDefinitionsChanged(handler: () => void): () => void {
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}
