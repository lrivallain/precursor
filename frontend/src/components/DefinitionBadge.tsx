import { FileCode2, FileWarning, FileX2 } from "lucide-react";
import type { DefinitionSource } from "../lib/types";

/**
 * Files mode only: which definition file declares an agent or workflow.
 * Renders nothing in database mode, where `source` is null.
 */
export function DefinitionBadge({ source }: { source: DefinitionSource | null | undefined }) {
  if (!source) return null;
  if (source.state === "file") {
    return (
      <span
        className="inline-flex max-w-[16rem] items-center gap-1 rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-600 dark:text-sky-400"
        data-tooltip={`Declared by ${source.path}.\nEdits made here aren't written to the file yet — edit the file.`}
      >
        <FileCode2 size={10} aria-hidden />
        <span className="truncate">{source.path}</span>
      </span>
    );
  }
  if (source.state === "invalid") {
    return (
      <span
        className="inline-flex max-w-[16rem] items-center gap-1 rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-600 dark:text-red-400"
        data-tooltip={source.message ?? "Its definition file has errors"}
      >
        <FileWarning size={10} aria-hidden />
        <span className="truncate">{source.path}</span>
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
      data-tooltip={source.message ?? "No definition file yet"}
    >
      <FileX2 size={10} aria-hidden />
      No file
    </span>
  );
}
