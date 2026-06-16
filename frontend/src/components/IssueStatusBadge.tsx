import { AlertCircle, CheckCircle2, Loader2, Unlink } from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import type { IssueContextStatus } from "../lib/useIssueContext";

interface Props {
  status: IssueContextStatus;
  onClick?: () => void;
  title?: string;
}

export function IssueStatusBadge({ status, onClick, title }: Props) {
  if (status === "idle") return null;

  let icon: React.ReactNode;
  let cls: string;
  let label: string;

  switch (status) {
    case "loading":
      icon = <Loader2 size={14} className="animate-spin" />;
      cls = "text-muted";
      label = "Refreshing issue context\u2026";
      break;
    case "ready":
      icon = <CheckCircle2 size={14} />;
      cls = "text-green-500";
      label = "Issue context up to date";
      break;
    case "error":
      icon = <AlertCircle size={14} />;
      cls = "text-red-500";
      label = "Failed to load issue context";
      break;
    case "no-issue":
      icon = <Github size={14} />;
      cls = "text-muted";
      label = "No GitHub issue linked";
      break;
    case "no-repo":
      icon = <Unlink size={14} />;
      cls = "text-amber-500";
      label = "Issue is set but no repository configured";
      break;
  }

  return (
    <button
      type="button"
      onClick={onClick}
      data-tooltip={title ?? label}
      aria-label={label}
      className={`inline-flex items-center ${cls} hover:opacity-80`}
    >
      {icon}
    </button>
  );
}
