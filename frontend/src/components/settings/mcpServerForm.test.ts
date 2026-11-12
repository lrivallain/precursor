import { describe, expect, it } from "vitest";
import {
  applyAuthDetection,
  azLoginCommand,
  mcpServerPayload,
  removeAt,
  replaceAt,
  type McpServerForm,
} from "./mcpServerForm";

const form: McpServerForm = {
  name: " my-server ",
  transport: "streamable_http",
  url: " https://example.com/mcp ",
  command: "npx",
  args: ["-y", "pkg"],
  headers: [{ key: " Authorization ", value: "Bearer x" }, { key: "  ", value: "dropped" }],
  auth: "none",
  authTenant: "",
  authResource: "",
};

describe("mcpServerPayload", () => {
  it("sends the URL and named headers for HTTP", () => {
    expect(mcpServerPayload(form)).toEqual({
      name: "my-server",
      transport: "streamable_http",
      url: "https://example.com/mcp",
      headers: { Authorization: "Bearer x" },
      auth: "none",
      auth_tenant: null,
      auth_resource: null,
    });
  });

  it("sends the trimmed tenant and resource for Azure CLI sign-in", () => {
    const payload = mcpServerPayload({
      ...form,
      auth: "azure_cli",
      authTenant: " contoso.onmicrosoft.com ",
      authResource: " api://abc ",
    });
    expect(payload).toMatchObject({
      auth: "azure_cli",
      auth_tenant: "contoso.onmicrosoft.com",
      auth_resource: "api://abc",
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

describe("applyAuthDetection", () => {
  const detected = { auth_required: true, message: "" } as const;

  it("switches to Azure CLI and fills the detected tenant and resource", () => {
    expect(
      applyAuthDetection(form, { ...detected, auth: "azure_cli", tenant: "t1", resource: "api://r" }),
    ).toEqual({ auth: "azure_cli", authTenant: "t1", authResource: "api://r" });
  });

  it("keeps typed values the server did not advertise", () => {
    const typed = { ...form, authResource: "api://typed" };
    expect(
      applyAuthDetection(typed, { ...detected, auth: "azure_cli", tenant: "", resource: null }),
    ).toEqual({ auth: "azure_cli", authTenant: "", authResource: "api://typed" });
  });

  it("changes nothing for a non-Entra server", () => {
    expect(applyAuthDetection(form, { ...detected, auth: "none", tenant: null, resource: null })).toEqual({});
  });
});

describe("azLoginCommand", () => {
  it("names the tenant and the resource's default scope", () => {
    expect(azLoginCommand(" t1 ", "api://r/")).toBe("az login --tenant t1 --scope api://r/.default");
    expect(azLoginCommand("", "")).toBe("az login --scope <resource>/.default");
  });
});
