import { describe, expect, it } from "vitest";
import { m365Enabled } from "./m365";

describe("m365Enabled", () => {
  it("is off until settings are loaded", () => {
    expect(m365Enabled(null)).toBe(false);
  });

  it("follows the WorkIQ server's enabled flag", () => {
    expect(m365Enabled({ mcp_enabled: { workiq: true } })).toBe(true);
    expect(m365Enabled({ mcp_enabled: { workiq: false } })).toBe(false);
    expect(m365Enabled({ mcp_enabled: { github: true } })).toBe(false);
  });
});
