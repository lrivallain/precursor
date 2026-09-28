// Change bars for the editor's gutter, from a unified diff against HEAD.

export interface LineChange {
  kind: "added" | "modified" | "deleted";
  /** 1-based lines of the working copy; a deletion sits after `start`. */
  start: number;
  end: number;
}

const HUNK = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/**
 * Classify a diff's changed lines the way VS Code's gutter does: lines only
 * added are "added", removed lines replaced by new ones are "modified", and
 * removed lines with nothing in their place leave a "deleted" mark.
 */
export function lineChanges(diff: string): LineChange[] {
  const changes: LineChange[] = [];
  let line = 0;
  let removed = 0;
  let added = 0;
  let inHunk = false;

  const flush = () => {
    if (added > 0) {
      changes.push({
        kind: removed > 0 ? "modified" : "added",
        start: line - added,
        end: line - 1,
      });
    } else if (removed > 0) {
      const at = Math.max(line - 1, 1);
      changes.push({ kind: "deleted", start: at, end: at });
    }
    removed = 0;
    added = 0;
  };

  for (const text of diff.split("\n")) {
    const hunk = HUNK.exec(text);
    if (hunk) {
      flush();
      line = Number(hunk[1]);
      inHunk = true;
      continue;
    }
    if (!inHunk || text.startsWith("\\")) continue;
    if (text.startsWith("+")) {
      added++;
      line++;
    } else if (text.startsWith("-")) {
      // A removal after additions starts a new run.
      if (added > 0) flush();
      removed++;
    } else if (text.startsWith(" ") || text === "") {
      flush();
      line++;
    } else {
      // "diff --git" of a next file, etc.
      flush();
      inHunk = false;
    }
  }
  flush();
  return changes;
}
