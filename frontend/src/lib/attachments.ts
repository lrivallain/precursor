const DOC_ATTACHMENT_MIMES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]);

const DOC_ATTACHMENT_SUFFIXES = [".pdf", ".docx", ".pptx"];

// ``application/*`` MIME types that are really UTF-8 text (mirrors the backend's
// ALLOWED_TEXT_APPLICATION_MIMES).
const TEXT_APPLICATION_MIMES = new Set([
  "application/json",
  "application/ld+json",
  "application/xml",
  "application/yaml",
  "application/x-yaml",
  "application/toml",
  "application/x-toml",
  "application/sql",
  "application/x-sql",
  "application/javascript",
  "application/x-javascript",
  "application/x-sh",
  "application/x-httpd-php",
  "application/graphql",
  "application/x-ndjson",
  "application/csv",
]);

// Text/code file extensions (mirrors the backend's _TEXT_MIME_BY_EXTENSION keys).
// Browsers report inconsistent MIME types for source files, so acceptance falls
// back to the extension.
const TEXT_ATTACHMENT_SUFFIXES = [
  ".txt", ".text", ".log", ".md", ".markdown", ".mdx", ".rst", ".csv", ".tsv",
  ".json", ".jsonl", ".ndjson", ".geojson", ".yaml", ".yml", ".toml", ".ini",
  ".cfg", ".conf", ".env", ".properties", ".xml", ".html", ".htm", ".css",
  ".scss", ".sass", ".less", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
  ".vue", ".svelte", ".py", ".pyi", ".rb", ".go", ".rs", ".java", ".kt",
  ".kts", ".scala", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cs",
  ".php", ".swift", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".sql",
  ".r", ".pl", ".lua", ".dart", ".tex", ".graphql", ".gql", ".proto",
  ".dockerfile", ".makefile", ".mk", ".gitignore", ".gradle",
];

export const ATTACHMENT_ACCEPT = `image/*,text/*,.pdf,.docx,.pptx,${TEXT_ATTACHMENT_SUFFIXES.join(",")}`;
export const SUPPORTED_ATTACHMENT_LABEL =
  "image/*, .pdf, .docx, .pptx, and text/code files (.txt, .md, .csv, .json, .py, …)";

export function isSupportedAttachmentFile(file: File): boolean {
  const mime = (file.type || "").toLowerCase().split(";", 1)[0].trim();
  if (mime.startsWith("image/")) return true;
  if (mime.startsWith("text/")) return true;
  if (DOC_ATTACHMENT_MIMES.has(mime)) return true;
  if (TEXT_APPLICATION_MIMES.has(mime)) return true;
  const lowerName = (file.name || "").toLowerCase();
  if (DOC_ATTACHMENT_SUFFIXES.some((suffix) => lowerName.endsWith(suffix))) return true;
  return TEXT_ATTACHMENT_SUFFIXES.some((suffix) => lowerName.endsWith(suffix));
}

export function splitSupportedAttachmentFiles(
  files: Iterable<File>,
): { supported: File[]; unsupported: string[] } {
  const supported: File[] = [];
  const unsupported: string[] = [];
  for (const file of files) {
    if (!file) continue;
    if (isSupportedAttachmentFile(file)) supported.push(file);
    else unsupported.push(file.name || "unnamed file");
  }
  return { supported, unsupported };
}

export function unsupportedAttachmentMessage(files: string[]): string {
  const preview = files.slice(0, 3).join(", ");
  const suffix = files.length > 3 ? ` (+${files.length - 3} more)` : "";
  return `Unsupported attachment type: ${preview}${suffix}. Supported types: ${SUPPORTED_ATTACHMENT_LABEL}`;
}
