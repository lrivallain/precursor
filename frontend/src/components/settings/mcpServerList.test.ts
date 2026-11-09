import { describe, expect, it } from "vitest";
import type { MCPServerStatus } from "../../lib/types";
import { patchServer, serverStateColor, signInOwners, signInRole } from "./mcpServerList";

const server = (name: string, oauth = false) => ({ name, oauth, state: "disabled" }) as MCPServerStatus;

describe("patchServer", () => {
  it("changes only the named server", () => {
    const list = [server("a"), server("b")];
    const next = patchServer(list, "b", { state: "error", error: "boom" });
    expect(next[0]).toBe(list[0]);
    expect(next[1]).toMatchObject({ name: "b", state: "error", error: "boom" });
  });
});

describe("sign-in owners", () => {
  const list = [server("local"), server("workiq-teams", true), server("workiq-word", true), server("workiq", true)];
  const owners = signInOwners(list);

  it("gives each credential family's first server the button", () => {
    expect(signInRole(list[1], owners)).toEqual({ owner: true });
    expect(signInRole(list[3], owners)).toEqual({ owner: true });
  });

  it("points siblings at the server they sign in with", () => {
    expect(signInRole(list[2], owners)).toEqual({ owner: false, sharedWith: "WorkIQ Teams" });
  });

  it("offers nothing for servers without OAuth", () => {
    expect(signInRole(list[0], owners)).toEqual({ owner: false });
  });
});

describe("serverStateColor", () => {
  it("maps states to status colours", () => {
    expect(serverStateColor("ready")).toBe("text-green-500");
    expect(serverStateColor("error")).toBe("text-red-500");
    expect(serverStateColor("needs_auth")).toBe("text-amber-500");
    expect(serverStateColor("disabled")).toBe("text-muted");
  });
});
