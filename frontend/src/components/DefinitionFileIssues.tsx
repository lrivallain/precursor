import { useEffect, useState } from "react";
import { CircleCheck, CircleAlert, TriangleAlert } from "lucide-react";
import { api } from "../lib/api";
import type { DefinitionFileReport, DefinitionIssue } from "../lib/types";

export function isDefinitionFile(path: string): boolean {
  const name = path.split("/").pop() ?? "";
  return !name.startsWith(".") && (name.endsWith(".agent.yaml") || name.endsWith(".workflow.yaml"));
}

/**
 * The definitions check's report on the open file, when it is an agent or
 * workflow definition. `version` bumps after each save so the report follows
 * the file on disk.
 */
export function useDefinitionReport(
  workspaceId: number,
  path: string | null,
  version: number,
): DefinitionFileReport | null {
  const [report, setReport] = useState<DefinitionFileReport | null>(null);

  useEffect(() => {
    let cancelled = false;
    setReport(null);
    if (!path || !isDefinitionFile(path)) return;
    api.definitions
      .fileIssues(workspaceId, path)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [workspaceId, path, version]);

  return report;
}

/** An issue the editor can point at: the check placed it in the text. */
export function isPlaced(issue: DefinitionIssue): issue is DefinitionIssue & {
  line: number;
  column: number;
  end_line: number;
  end_column: number;
} {
  return (
    issue.line != null &&
    issue.column != null &&
    issue.end_line != null &&
    issue.end_column != null
  );
}

/**
 * Under the Files editor: the definitions check's findings for the open file,
 * when it is an agent or workflow definition inside the definitions folder.
 * A finding placed in the text moves the editor to it on click.
 */
export function DefinitionFileIssues({
  report,
  onReveal,
}: {
  report: DefinitionFileReport | null;
  onReveal?: (line: number, column: number) => void;
}) {
  if (!report?.in_definitions) return null;
  const errors = report.issues.filter((i) => i.severity === "error");
  const warnings = report.issues.filter((i) => i.severity === "warning");
  const summary =
    report.issues.length === 0
      ? `Valid ${report.kind} definition`
      : [
          errors.length ? `${errors.length} error${errors.length > 1 ? "s" : ""}` : null,
          warnings.length ? `${warnings.length} warning${warnings.length > 1 ? "s" : ""}` : null,
        ]
          .filter(Boolean)
          .join(", ");

  return (
    <div
      className="max-h-40 shrink-0 overflow-auto border-t border-border bg-surface/40 px-4 py-2 text-xs"
      aria-label="Definition check"
    >
      <div className="flex items-center gap-1.5 font-medium">
        {errors.length ? (
          <CircleAlert size={13} className="text-red-500" aria-hidden />
        ) : warnings.length ? (
          <TriangleAlert size={13} className="text-amber-500" aria-hidden />
        ) : (
          <CircleCheck size={13} className="text-emerald-500" aria-hidden />
        )}
        <span>{summary}</span>
        <span className="font-normal text-muted">· checked on save</span>
      </div>
      {report.issues.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {report.issues.map((issue, i) => {
            const body = (
              <>
                <span
                  className={`shrink-0 ${
                    issue.severity === "error" ? "text-red-500" : "text-amber-500"
                  }`}
                >
                  {issue.severity}
                </span>
                {issue.location && (
                  <code className="shrink-0 text-muted">{issue.location}</code>
                )}
                <span>{issue.message}</span>
                {isPlaced(issue) && (
                  <span className="ml-auto shrink-0 whitespace-nowrap pl-2 text-muted">
                    line {issue.line}
                  </span>
                )}
              </>
            );
            return (
              <li key={i}>
                {isPlaced(issue) && onReveal ? (
                  <button
                    type="button"
                    className="flex w-full gap-1.5 rounded text-left hover:bg-surface"
                    data-tooltip="Show in the editor"
                    onClick={() => onReveal(issue.line, issue.column)}
                  >
                    {body}
                  </button>
                ) : (
                  <div className="flex gap-1.5">{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
