/**
 * Turn rendered `.markdown` into HTML that keeps its layout once pasted into an
 * email. Precursor's spacing lives in the `.markdown` stylesheet, which the
 * clipboard never carries: the draft looks right because the editor applies
 * browser defaults, then mail clients reset them on send (OWA injects
 * `P {margin-top:0;margin-bottom:0}`, Outlook's Word engine `MsoNormal
 * {margin:0}`). Only inline `style="…"` reliably survives, so every block gets
 * explicit margins here. Colours are fixed light values rather than theme
 * variables — the recipient doesn't share our dark mode.
 */

const MONO = "Menlo,Consolas,'Courier New',monospace";
const BORDER = "#d0d7de";
const SUBTLE_BG = "#f6f8fa";
const MUTED = "#59636e";
const GAP = "12px";

const HEADING_SIZES: Record<string, string> = {
  H1: "22px",
  H2: "19px",
  H3: "17px",
  H4: "15px",
  H5: "14px",
  H6: "13px",
};

// Blocks that get the inter-block gap, mirroring index.css's
// `.markdown :where(p, ul, …):not(:last-child)` rule.
const SPACED = new Set([
  "P", "UL", "OL", "PRE", "BLOCKQUOTE", "TABLE", "HR", "DIV",
  "H1", "H2", "H3", "H4", "H5", "H6",
]);

const KEEP_ATTRS: Record<string, string[]> = {
  A: ["href"],
  IMG: ["src", "alt", "width", "height"],
  OL: ["start"],
  TD: ["colspan", "rowspan"],
  TH: ["colspan", "rowspan"],
};

function unwrap(el: Element): void {
  el.replaceWith(...Array.from(el.childNodes));
}

function absolutize(value: string): string {
  try {
    return new URL(value, document.baseURI).href;
  } catch {
    return value;
  }
}

function blockStyle(el: Element, last: boolean): string {
  const bottom = last ? "0" : GAP;
  switch (el.tagName) {
    case "H1":
    case "H2":
    case "H3":
    case "H4":
    case "H5":
    case "H6": {
      const top = el.previousElementSibling ? "18px" : "0";
      const rule =
        el.tagName === "H1" || el.tagName === "H2"
          ? `padding:0 0 4px 0;border-bottom:1px solid ${BORDER};`
          : "";
      const muted = el.tagName === "H6" ? `color:${MUTED};` : "";
      return `margin:${top} 0 ${bottom} 0;${rule}${muted}font-size:${HEADING_SIZES[el.tagName]};font-weight:600;line-height:1.25`;
    }
    case "UL":
    case "OL": {
      const nested = el.parentElement?.tagName === "LI";
      const margin = nested ? "4px 0 0 0" : `0 0 ${bottom} 0`;
      const marker = el.tagName === "UL" ? "disc" : "decimal";
      return `margin:${margin};padding:0 0 0 24px;list-style-type:${marker}`;
    }
    case "PRE":
      return `margin:0 0 ${bottom} 0;padding:12px;background:${SUBTLE_BG};border:1px solid ${BORDER};border-radius:6px;font-family:${MONO};font-size:13px;line-height:1.45;white-space:pre-wrap;word-wrap:break-word`;
    case "BLOCKQUOTE":
      return `margin:0 0 ${bottom} 0;padding:0 0 0 12px;border-left:3px solid ${BORDER};color:${MUTED}`;
    case "TABLE":
      return `margin:0 0 ${bottom} 0;border-collapse:collapse`;
    case "HR":
      return `margin:0 0 ${bottom} 0;height:0;border:0;border-top:1px solid ${BORDER}`;
    case "DIV":
      // Only callouts survive as divs (plain wrappers are unwrapped).
      return `margin:0 0 ${bottom} 0;padding:8px 12px;background:#fff8c5;border:1px solid #d4a72c;border-radius:6px`;
    default:
      return `margin:0 0 ${bottom} 0`;
  }
}

function inlineStyle(el: Element): string | null {
  switch (el.tagName) {
    case "LI":
      return el.nextElementSibling ? "margin:0 0 4px 0" : "margin:0";
    case "TH":
      return `padding:6px 10px;border:1px solid ${BORDER};background:${SUBTLE_BG};font-weight:600;text-align:left;vertical-align:top`;
    case "TD":
      return `padding:6px 10px;border:1px solid ${BORDER};text-align:left;vertical-align:top`;
    case "CODE":
      return el.parentElement?.tagName === "PRE"
        ? `font-family:${MONO};font-size:13px;background:transparent;padding:0`
        : `font-family:${MONO};font-size:0.9em;background:${SUBTLE_BG};padding:1px 4px;border-radius:4px`;
    case "A":
      return "color:#0969da;text-decoration:underline";
    case "IMG":
      return "max-width:100%;height:auto";
    default:
      return null;
  }
}

/** Email-safe HTML for a rendered `.markdown` element (or a wrapper of one). */
export function toEmailHtml(source: Element): string {
  const root = (source.matches(".markdown") ? source : source.querySelector(".markdown") ?? source)
    .cloneNode(true) as HTMLElement;

  // Diagrams render as inline SVG, which mail clients drop: send their source.
  root.querySelectorAll("[data-copy-source]").forEach((el) => {
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = el.getAttribute("data-copy-source") ?? "";
    pre.append(code);
    el.replaceWith(pre);
  });
  root.querySelectorAll("button, svg, script, style, .sr-only").forEach((el) => el.remove());
  const taskItems = new Set<Element>();
  root.querySelectorAll("input[type=checkbox]").forEach((el) => {
    const li = el.closest("li");
    if (li) taskItems.add(li);
    el.replaceWith(document.createTextNode((el as HTMLInputElement).checked ? "☑" : "☐"));
  });
  root.querySelectorAll("label, mark").forEach(unwrap);
  // Highlighted code is a soup of class-coloured spans; keep the text only.
  root.querySelectorAll("pre code").forEach((code) => {
    code.textContent = (code.textContent ?? "").replace(/\n$/, "");
  });
  // Layout wrappers (code-block shell, callout internals) carry no meaning.
  root.querySelectorAll("div:not([data-callout])").forEach(unwrap);

  root.querySelectorAll("*").forEach((el) => {
    const keep = KEEP_ATTRS[el.tagName] ?? [];
    for (const { name } of Array.from(el.attributes)) {
      if (!keep.includes(name)) el.removeAttribute(name);
    }
    for (const name of ["href", "src"]) {
      const value = el.getAttribute(name);
      if (value && !value.startsWith("#")) el.setAttribute(name, absolutize(value));
    }
    const style = SPACED.has(el.tagName)
      ? blockStyle(el, !el.nextElementSibling)
      : inlineStyle(el);
    if (style) {
      el.setAttribute("style", taskItems.has(el) ? `${style};list-style-type:none` : style);
    }
  });
  // Source-offset annotations leave bare spans behind; they only add noise.
  root.querySelectorAll("span:not([style])").forEach(unwrap);

  return `<div>${root.innerHTML}</div>`;
}

/**
 * Put rendered markdown on the clipboard as email-safe HTML, with `plain` as
 * the text/plain flavour. Returns false when the clipboard is unavailable.
 */
export async function copyFormatted(source: Element, plain: string): Promise<boolean> {
  try {
    const html = toEmailHtml(source);
    if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([plain], { type: "text/plain" }),
        }),
      ]);
    } else {
      await navigator.clipboard.writeText(plain);
    }
    return true;
  } catch {
    return false;
  }
}
