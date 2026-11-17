// What the Files section can do with a file, from its name.

const TEXT_EXTS = [
  ".md",
  ".markdown",
  ".txt",
  ".rst",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
  ".csv",
  ".html",
  ".css",
  ".js",
  ".ts",
  ".py",
  ".sh",
  ".drawio",
  ".drawio.xml",
  ".mmd",
  ".svg",
];

export function isEditable(name: string): boolean {
  const lower = name.toLowerCase();
  return TEXT_EXTS.some((e) => lower.endsWith(e)) || lower.startsWith(".");
}

export function isMarkdown(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".md") || lower.endsWith(".markdown");
}

export function isHtml(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".html") || lower.endsWith(".htm");
}

// Extensions the drawio MCP server writes (services/mcp/drawio_server.py).
export function isDrawio(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".drawio") || lower.endsWith(".drawio.xml");
}

export function isMermaid(name: string): boolean {
  return name.toLowerCase().endsWith(".mmd");
}

export function isSvg(name: string): boolean {
  return name.toLowerCase().endsWith(".svg");
}

export function hasPreview(name: string): boolean {
  return isMarkdown(name) || isHtml(name) || isDrawio(name) || isMermaid(name) || isSvg(name);
}

// SVG stays out: it's text, editable as such, with a rendered preview.
const IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif"];

export function isImage(name: string): boolean {
  const lower = name.toLowerCase();
  return IMAGE_EXTS.some((e) => lower.endsWith(e));
}

export function isPdf(name: string): boolean {
  return name.toLowerCase().endsWith(".pdf");
}
