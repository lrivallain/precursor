import { describe, expect, it } from "vitest";
import { browseOpensDrawer } from "./sections";

describe("browseOpensDrawer", () => {
  it("opens the drawer for sections listed in the sidebar tree", () => {
    for (const mode of ["topics", "chats", "live", "workspaces"]) {
      expect(browseOpensDrawer(mode)).toBe(true);
    }
  });

  it("keeps it closed for sections with their own overview", () => {
    for (const mode of ["agents", "workflows", "steps", "iq"]) {
      expect(browseOpensDrawer(mode)).toBe(false);
    }
  });
});
