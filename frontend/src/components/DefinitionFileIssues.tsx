import { useEffect, useState } from "react";
import { CircleCheck, CircleAlert, TriangleAlert } from "lucide-react";
import { api } from "../lib/api";
import type { DefinitionFileReport } from "../lib/types";

export function isDefinitionFile(path: string): boolean {
  const name = path.split("/").pop() ?? "";
  return !name.startsWith(".") && (name.endsWith(".agent.yaml") || name.endsWith(".workflow.yaml"));
}

/**
 * Under the Files editor: the definitions check's findings for the open file,
 * when it is an agent or workflow definition inside the definitions folder.
 * `version` bumps after each save so the findings follow the file on disk.
 */
export function DefinitionFileIssues({
  workspaceId,
  path,
  version,
}: {
  workspaceId: number;
  path: string;
  version: number;
}) {
  const [report, setReport] = useState<DefinitionFileReport | null>(null);

  useEffect(() => {
    let cancelled = false;
    setReport(null);
    if (!isDefinitionFile(path)) return;
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
          {report.issues.map((issue, i) => (
            <li key={i} className="flex gap-1.5">
              <span
                className={issue.severity === "error" ? "text-red-500" : "text-amber-500"}
              >
                {issue.severity}
              </span>
              {issue.location && <code className="text-muted">{issue.location}</code>}
              <span>{issue.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
