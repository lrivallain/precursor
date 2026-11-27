// One request at a time: a call made while the previous one is still running
// shares its result instead of starting another. Used for pairing codes, where
// every new code invalidates the one before it, so two overlapping requests
// could leave the older (now invalid) code on screen.

export function singleFlight<T>(run: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | null = null;
  return () => {
    pending ??= run().finally(() => {
      pending = null;
    });
    return pending;
  };
}
