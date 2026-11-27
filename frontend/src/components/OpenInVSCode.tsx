import { SquareCode } from "lucide-react";
import { vscodeUrl } from "../lib/localPath";
import { useRemoteContext } from "../lib/mobileAccess";

const VSCODE_HINT =
  "\nOpens the server's copy, so VS Code must run on the same computer.";

export function OpenInVSCode({
  path,
  label,
  size,
  position,
}: {
  /** Absolute path on the server. */
  path: string;
  label: string;
  size: number;
  /** Where the cursor is, read at click time so the link lands on that line. */
  position?: () => { line: number; column: number } | null;
}) {
  // A vscode:// link to a path on the computer: nothing it can open on a phone
  // that reached Precursor through the remote-access tunnel.
  if (useRemoteContext()) return null;
  return (
    <a
      className="p-1 rounded text-muted hover:text-text hover:bg-surface"
      href={vscodeUrl(path)}
      aria-label={label}
      data-tooltip={label + VSCODE_HINT}
      onClick={(e) => {
        // The link follows its href after this handler, so it lands on the line.
        const at = position?.();
        e.currentTarget.href = vscodeUrl(path, at?.line, at?.column);
      }}
    >
      <SquareCode size={size} />
    </a>
  );
}
