import { describe, expect, it } from "vitest";
import { mcpServerPayload, removeAt, replaceAt, type McpServerForm } from "./mcpServerForm";

const form: McpServerForm = {
  name: " my-server ",
  transport: "streamable_http",
  url: " https://example.com/mcp ",
  command: "npx",
  args: ["-y", "pkg"],
  headers: [{ key: " Authorization ", value: "Bearer x" }, { key: "  ", value: "dropped" }],
};

describe("mcpServerPayload", () => {
  it("sends the URL and named headers for HTTP", () => {
    expect(mcpServerPayload(form)).toEqual({
      name: "my-server",
      transport: "streamable_http",
      url: "https://example.com/mcp",
      headers: { Authorization: "Bearer x" },
    });
  });

  it("sends the command and arguments for stdio", () => {
    expect(mcpServerPayload({ ...form, transport: "stdio", command: " npx " })).toEqual({
      name: "my-server",
      transport: "stdio",
      command: "npx",
      args: ["-y", "pkg"],
    });
  });
});

describe("row helpers", () => {
  it("replace and remove by index", () => {
    expect(replaceAt(["a", "b", "c"], 1, "x")).toEqual(["a", "x", "c"]);
    expect(removeAt(["a", "b", "c"], 0)).toEqual(["b", "c"]);
  });
});
