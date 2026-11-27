// Overlapping requests whose answers may come back out of order: only an answer
// newer than the last one applied is applied, so a slow, older answer can't
// roll the state back.

export interface LatestOnly {
  /** A ticket for a request about to start. */
  next: () => number;
  /** Whether that request's answer is still the newest; if so it becomes the last applied. */
  accept: (ticket: number) => boolean;
}

export function latestOnly(): LatestOnly {
  let issued = 0;
  let applied = 0;
  return {
    next: () => ++issued,
    accept: (ticket) => {
      if (ticket <= applied) return false;
      applied = ticket;
      return true;
    },
  };
}
