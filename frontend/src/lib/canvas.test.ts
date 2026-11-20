import { describe, expect, it } from "vitest";
import {
  MAX_FRAME_HEIGHT,
  MIN_FRAME_HEIGHT,
  canvasKind,
  overflowOverride,
  canvasRawUrl,
  parseCanvasRef,
  readCanvasMessage,
} from "./canvas";

const CONV = "0f8fad5b-d9cb-469f-a165-70867728950e";

describe("parseCanvasRef", () => {
  it("reads a canvas path of each kind", () => {
    expect(parseCanvasRef(`${CONV}/slow-start.canvas.html\n`)).toEqual({
      path: `${CONV}/slow-start.canvas.html`,
      conversationId: CONV,
      name: "slow-start",
      kind: "html",
    });
    expect(parseCanvasRef(`${CONV}/arch.drawio`)?.kind).toBe("drawio");
    expect(parseCanvasRef(`/${CONV}/flow.mmd`)?.kind).toBe("mermaid");
    expect(parseCanvasRef(`${CONV}/logo.svg`)?.name).toBe("logo");
  });

  it("refuses anything that is not a canvas path", () => {
    expect(parseCanvasRef("graph TD; A-->B")).toBeNull();
    expect(parseCanvasRef(`${CONV}/page.html`)).toBeNull();
    expect(parseCanvasRef(`../${CONV}/a.svg`)).toBeNull();
    expect(parseCanvasRef(`not-a-uuid/a.svg`)).toBeNull();
    expect(parseCanvasRef(`${CONV}/.state/a.svg`)).toBeNull();
    expect(parseCanvasRef(`${CONV}/Bad Name.svg`)).toBeNull();
  });
});

describe("canvas urls", () => {
  it("builds the raw link", () => {
    expect(canvasKind("A.CANVAS.HTML")).toBe("html");
    expect(canvasRawUrl(`${CONV}/a.svg`)).toBe(`/raw/canvas/${CONV}/a.svg`);
    expect(canvasRawUrl(`${CONV}/a.svg`, 3)).toBe(`/raw/canvas/${CONV}/a.svg?v=3`);
  });
});

describe("readCanvasMessage", () => {
  it("clamps heights and keeps asks", () => {
    expect(readCanvasMessage({ type: "precursor:canvas-resize", height: 10 })).toEqual({
      type: "precursor:canvas-resize",
      height: MIN_FRAME_HEIGHT,
    });
    expect(readCanvasMessage({ type: "precursor:canvas-resize", height: 1e9 })?.type).toBe(
      "precursor:canvas-resize",
    );
    expect(
      (readCanvasMessage({ type: "precursor:canvas-resize", height: 1e9 }) as { height: number })
        .height,
    ).toBe(MAX_FRAME_HEIGHT);
    expect(readCanvasMessage({ type: "precursor:canvas-ask", text: "Why?" })).toEqual({
      type: "precursor:canvas-ask",
      text: "Why?",
    });
  });

  it("ignores other messages", () => {
    expect(readCanvasMessage(null)).toBeNull();
    expect(readCanvasMessage("hi")).toBeNull();
    expect(readCanvasMessage({ type: "precursor:canvas-ask", text: "  " })).toBeNull();
    expect(readCanvasMessage({ type: "other", height: 3 })).toBeNull();
  });
});

function fakeStyle(value = "", priority = "") {
  const style = {
    value,
    priority,
    getPropertyValue: () => style.value,
    getPropertyPriority: () => style.priority,
    setProperty: (_name: string, v: string | null, p?: string) => {
      style.value = v ?? "";
      style.priority = p ?? "";
    },
  };
  return style;
}

describe("overflowOverride", () => {
  it("hides the scrollbar and puts the page's own value back", () => {
    const style = fakeStyle("scroll", "important");
    const override = overflowOverride();
    override.apply(style);
    expect([style.value, style.priority]).toEqual(["hidden", "important"]);
    override.restore();
    expect([style.value, style.priority]).toEqual(["scroll", "important"]);
  });

  it("keeps the original value when applied twice to the same page", () => {
    // The height can be reported before the frame's load event re-applies.
    const style = fakeStyle("auto");
    const override = overflowOverride();
    override.apply(style);
    override.apply(style);
    override.restore();
    expect([style.value, style.priority]).toEqual(["auto", ""]);
  });

  it("restores the page it was last applied to", () => {
    const first = fakeStyle("auto");
    const second = fakeStyle("");
    const override = overflowOverride();
    override.apply(first);
    override.apply(second);
    expect(first.value).toBe("auto");
    override.restore();
    expect(second.value).toBe("");
  });
});
