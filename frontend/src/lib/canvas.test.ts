import { describe, expect, it } from "vitest";
import {
  MAX_FRAME_HEIGHT,
  MIN_FRAME_HEIGHT,
  canvasKind,
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
