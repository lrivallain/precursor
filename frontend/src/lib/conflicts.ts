// Conflict blocks git writes into a file during a merge (the two-sided
// `merge.conflictStyle=merge`, which the app always asks for), and how to
// resolve one.
//
//   <<<<<<< HEAD                 ← start
//   our lines
//   =======                      ← middle
//   their lines
//   >>>>>>> origin/main          ← end

export interface ConflictBlock {
  /** 1-based line numbers of the three markers. */
  start: number;
  middle: number;
  end: number;
  /** What git wrote after the markers, e.g. "HEAD" / "origin/main". */
  oursLabel: string;
  theirsLabel: string;
}

export type ConflictChoice = "current" | "incoming" | "both";

const START = /^<{7}(?:\s(.*))?$/;
const MIDDLE = /^={7}\s*$/;
const END = /^>{7}(?:\s(.*))?$/;

export function findConflicts(lines: string[]): ConflictBlock[] {
  const blocks: ConflictBlock[] = [];
  let start = 0;
  let middle = 0;
  let oursLabel = "";
  lines.forEach((text, i) => {
    const line = i + 1;
    const opened = START.exec(text);
    if (opened) {
      start = line;
      middle = 0;
      oursLabel = (opened[1] ?? "").trim();
    } else if (start && !middle && MIDDLE.test(text)) {
      middle = line;
    } else if (start && middle) {
      const closed = END.exec(text);
      if (closed) {
        blocks.push({ start, middle, end: line, oursLabel, theirsLabel: (closed[1] ?? "").trim() });
        start = 0;
        middle = 0;
      }
    }
  });
  return blocks;
}

/** The lines that replace a whole block (markers included) for a choice. */
export function resolution(lines: string[], block: ConflictBlock, choice: ConflictChoice): string[] {
  const ours = lines.slice(block.start, block.middle - 1);
  const theirs = lines.slice(block.middle, block.end - 1);
  if (choice === "current") return ours;
  if (choice === "incoming") return theirs;
  return [...ours, ...theirs];
}
