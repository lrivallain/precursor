const DOC_ATTACHMENT_MIMES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]);

const DOC_ATTACHMENT_SUFFIXES = [".pdf", ".docx", ".pptx"];

export const ATTACHMENT_ACCEPT = "image/*,.pdf,.docx,.pptx";
export const SUPPORTED_ATTACHMENT_LABEL = "image/*, .pdf, .docx, .pptx";

export function isSupportedAttachmentFile(file: File): boolean {
  const mime = (file.type || "").toLowerCase();
  if (mime.startsWith("image/")) return true;
  if (DOC_ATTACHMENT_MIMES.has(mime)) return true;
  const lowerName = (file.name || "").toLowerCase();
  return DOC_ATTACHMENT_SUFFIXES.some((suffix) => lowerName.endsWith(suffix));
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
