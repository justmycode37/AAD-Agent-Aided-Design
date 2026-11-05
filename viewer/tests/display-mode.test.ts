import { describe, expect, it } from "vitest";
import { displayStates, type DisplayMode, type Visibility } from "../src/display-mode";

describe("display style and component visibility", () => {
  it.each<DisplayMode>(["shaded", "shaded-edges", "wireframe", "transparent"])("keeps hidden parts hidden in %s mode", mode => {
    const states: Record<string, Visibility> = { housing: [0, 0], gear: [1, 1] };
    const original = structuredClone(states);
    const next = displayStates(states, mode);
    expect(next.housing).toEqual([0, 0]);
    expect(next.gear).toEqual(mode === "wireframe" ? [0, 1] : mode === "shaded" ? [1, 0] : [1, 1]);
    expect(states).toEqual(original);
  });
  it("preserves absent surfaces and edges", () => {
    expect(displayStates({ curve: [3, 1], surface: [1, 3] }, "shaded-edges")).toEqual({ curve: [3, 1], surface: [1, 3] });
  });
});
