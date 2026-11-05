import { describe, expect, it } from "vitest";
import { resolveParts } from "../src/presentation";

describe("agent part selectors", () => {
  const ids = ["/Group/gears/gear", "/Group/gears/shaft", "/Group/housing"];
  it("resolves exact names and expands group paths", () => {
    expect(resolveParts(["gear", "/Group/gears"], ids)).toEqual(ids.slice(0, 2));
  });
  it("does not select similar-looking names or prefixes", () => {
    expect(() => resolveParts(["gea"], ids)).toThrow("Unknown part");
    expect(() => resolveParts(["/Group/gear"], ids)).toThrow("Unknown part");
  });
  it("rejects ambiguous short names", () => {
    expect(() => resolveParts(["gear"], [...ids, "/Other/gear"])).toThrow("full path");
  });
});
