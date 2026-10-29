// Absolute paths of workspace files on the server, and `vscode://` links to them.

/** `root` is the working copy; `subdir` the part of it the file tree shows. */
export function workspaceAbsolutePath(
  root: string,
  subdir: string | null,
  rel = "",
): string {
  // Join with the root's own separator so a copied Windows path stays native.
  const sep = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  const parts = [subdir ?? "", rel]
    .flatMap((p) => p.split(/[\\/]+/))
    .filter(Boolean);
  const base = root.replace(/[\\/]+$/, "");
  return parts.length ? [base, ...parts].join(sep) : base;
}

/**
 * `vscode://file/<path>[:line[:column]]`, which VS Code opens on the machine
 * running the browser — so it only finds the server's copy when both are the
 * same computer.
 */
export function vscodeUrl(absolutePath: string, line?: number, column?: number): string {
  let path = absolutePath.replace(/\\/g, "/");
  // Windows `C:/x` → `/C:/x`; POSIX paths already start with a slash.
  if (!path.startsWith("/")) path = `/${path}`;
  const encoded = path
    .split("/")
    .map((segment) =>
      /^[A-Za-z]:$/.test(segment) ? segment : encodeURIComponent(segment),
    )
    .join("/");
  const position = line ? `:${line}${column ? `:${column}` : ""}` : "";
  return `vscode://file${encoded}${position}`;
}
