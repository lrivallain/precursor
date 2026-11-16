import { describe, expect, it } from "vitest";
import { resolveLinkedFile } from "./workspaceLink";

const files = [
  { path: "customers", type: "dir" },
  { path: "customers/Club Med", type: "dir" },
  { path: "customers/Club Med/dns.drawio", type: "file" },
  { path: "notes/a.md", type: "file" },
  { path: "notes/A.md", type: "file" },
];

describe("resolveLinkedFile", () => {
  it("returns an exact match", () => {
    expect(resolveLinkedFile(files, "notes/a.md")).toBe("notes/a.md");
  });

  it("falls back to a case-insensitive match", () => {
    expect(resolveLinkedFile(files, "Customers/Club Med/dns.drawio")).toBe(
      "customers/Club Med/dns.drawio",
    );
  });

  it("refuses an ambiguous case-insensitive match", () => {
    expect(resolveLinkedFile(files, "NOTES/a.md")).toBeNull();
  });

  it("ignores directories and missing paths", () => {
    expect(resolveLinkedFile(files, "Customers/club med")).toBeNull();
    expect(resolveLinkedFile(files, "missing.md")).toBeNull();
  });
});
