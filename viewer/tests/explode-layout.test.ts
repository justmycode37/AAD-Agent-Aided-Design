import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { planExplodeLayout, type ExplodeLayout, type ExplodePart } from "../src/explode-layout";

const box = (id: string, cx: number, cy: number, cz: number, sx: number, sy: number, sz: number): ExplodePart => ({
  id,
  min: [cx - sx / 2, cy - sy / 2, cz - sz / 2],
  max: [cx + sx / 2, cy + sy / 2, cz + sz / 2],
});

function moved(parts: readonly ExplodePart[], layout: ExplodeLayout) {
  return parts.map(part => {
    const offset = layout.offsets.get(part.id)!;
    return { id: part.id, min: part.min.map((x, i) => x + offset[i]), max: part.max.map((x, i) => x + offset[i]) };
  });
}

/** Smallest separation between any two final AABBs; negative means they intersect. */
function smallestSeparation(parts: readonly ExplodePart[], layout: ExplodeLayout): number {
  const placed = moved(parts, layout);
  let smallest = Infinity;
  for (let a = 0; a < placed.length; a++) {
    for (let b = a + 1; b < placed.length; b++) {
      let separation = -Infinity;
      for (let i = 0; i < 3; i++) separation = Math.max(separation, placed[a].min[i] - placed[b].max[i], placed[b].min[i] - placed[a].max[i]);
      smallest = Math.min(smallest, separation);
    }
  }
  return smallest;
}

function expectClear(parts: readonly ExplodePart[], layout: ExplodeLayout) {
  expect(layout.offsets.size).toBe(parts.length);
  expect(smallestSeparation(parts, layout)).toBeGreaterThanOrEqual(layout.gap);
}

function overallSize(parts: readonly ExplodePart[], layout?: ExplodeLayout) {
  const placed = layout ? moved(parts, layout) : parts;
  return [0, 1, 2].map(i => Math.max(...placed.map(p => p.max[i])) - Math.min(...placed.map(p => p.min[i])));
}

const gearbox: ExplodePart[] = JSON.parse(readFileSync(new URL("./fixtures/gearbox-part-bounds.json", import.meta.url), "utf8"));
const gearboxId = (name: string) => `/Group/${name}`;

describe("real gearbox bounds", () => {
  const layout = planExplodeLayout(gearbox);
  const offset = (name: string) => layout.offsets.get(gearboxId(name))!;

  it("separates all 90 visible pieces along Z", () => {
    expect(gearbox).toHaveLength(90);
    expect(layout.axis).toBe(2);
    expectClear(gearbox, layout);
  });

  it("stays compact", () => {
    const before = overallSize(gearbox);
    const after = overallSize(gearbox, layout);
    expect(after[0] / before[0]).toBeLessThan(1.5);
    expect(after[1] / before[1]).toBeLessThan(3);
    expect(after[2] / before[2]).toBeLessThan(2.5);
  });

  it("keeps shafts at their axial station and in parallel lanes", () => {
    expect(offset("input_shaft")[2]).toBe(0);
    expect(offset("output_shaft")[2]).toBe(0);
    expect(offset("input_shaft")[1]).toBeCloseTo(offset("output_shaft")[1]);
    expect(offset("output_shaft")[0] - offset("input_shaft")[0]).toBeGreaterThan(0);
  });

  it("opens the split housing along its split direction", () => {
    expect(offset("housing_upper")[1]).toBeGreaterThan(0);
    expect(offset("housing_lower")[1]).toBeLessThan(0);
    expect(offset("housing_upper")[0]).toBeCloseTo(0);
    expect(offset("housing_upper")[2]).toBe(0);
    expect(offset("housing_lower")[0]).toBeCloseTo(0);
    expect(offset("housing_lower")[2]).toBe(0);
  });

  it("keeps a coaxial stack on one axis, in its original order", () => {
    const stack = ["bearing_output_rear_inner", "bearing_output_rear_outer", "seal_output", "output_flange"];
    for (const name of stack) {
      expect(offset(name)[0]).toBeCloseTo(offset("output_shaft")[0]);
      expect(offset(name)[1]).toBeCloseTo(offset("output_shaft")[1]);
    }
    const positions = stack.map(name => offset(name)[2] + gearbox.find(p => p.id === gearboxId(name))!.min[2]);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("moves bearing balls as a ring with their race", () => {
    const balls = Array.from({ length: 9 }, (_, i) => offset(`bearing_input_rear_ball_${i}`));
    expect(new Set(balls.map(ball => ball[2])).size).toBe(1);
    const center = [0, 1].map(i => balls.reduce((sum, ball) => sum + ball[i], 0) / balls.length);
    expect(center[0]).toBeCloseTo(offset("bearing_input_rear_inner")[0], 1);
    expect(center[1]).toBeCloseTo(offset("bearing_input_rear_inner")[1], 1);
    const start = (name: string) => gearbox.find(p => p.id === gearboxId(name))!.min[2] + offset(name)[2];
    expect(start("bearing_input_rear_ball_0")).toBeGreaterThan(start("bearing_input_rear_inner"));
    expect(start("bearing_input_rear_ball_0")).toBeLessThan(start("bearing_input_rear_outer"));
  });
});

describe("explode layout", () => {
  it("handles empty and single-part input", () => {
    expect(planExplodeLayout([]).offsets.size).toBe(0);
    expect(planExplodeLayout([box("a", 1, 2, 3, 4, 5, 6)]).offsets.get("a")).toEqual([0, 0, 0]);
  });

  it("stacks concentric nested parts along the axis without leaving the axis", () => {
    const parts = [box("shaft", 0, 0, 0, 10, 10, 200), box("race", 0, 0, 10, 20, 20, 20), box("gear", 0, 0, 12, 60, 60, 24), box("hub", 0, 0, -50, 30, 30, 18)];
    const layout = planExplodeLayout(parts);
    expectClear(parts, layout);
    expect(layout.axis).toBe(2);
    expect(layout.offsets.get("shaft")).toEqual([0, 0, 0]);
    for (const part of parts) expect(layout.offsets.get(part.id)!.slice(0, 2)).toEqual([0, 0]);
  });

  it("breaks exact ties by id, regardless of input order", () => {
    const parts = ["c", "a", "b", "d"].map(id => box(id, 5, 5, 5, 10, 10, 10));
    const layout = planExplodeLayout(parts);
    expectClear(parts, layout);
    const flipped = planExplodeLayout([...parts].reverse());
    for (const id of ["a", "b", "c", "d"]) expect(flipped.offsets.get(id)).toEqual(layout.offsets.get(id));
  });

  it("preserves axial order on each side of the anchor", () => {
    const stations = [-80, -55, -30, 30, 55, 80];
    const parts = [box("shaft", 0, 0, 0, 5, 5, 200), ...stations.map(z => box(`z${z}`, 0, 0, z, 30, 30, 10))];
    const layout = planExplodeLayout(parts);
    expectClear(parts, layout);
    const finals = stations.map(z => z + layout.offsets.get(`z${z}`)![2]);
    expect(finals).toEqual([...finals].sort((a, b) => a - b));
    expect(finals[2]).toBeLessThan(-100);
    expect(finals[3]).toBeGreaterThan(100);
  });

  it("keeps a part on the side of the anchor it already clears", () => {
    const parts = [box("shaft", 0, 0, 0, 5, 5, 100), box("a", 0, 0, 70, 5, 5, 10), box("b", 0, 0, 90, 5, 5, 10), box("c", 0, 0, 110, 5, 5, 10)];
    const layout = planExplodeLayout(parts);
    expectClear(parts, layout);
    const finals = ["a", "b", "c"].map((id, i) => [70, 90, 110][i] + layout.offsets.get(id)![2]);
    expect(finals[0]).toBeGreaterThan(50);
    expect(finals[0]).toBeLessThan(finals[1]);
    expect(finals[1]).toBeLessThan(finals[2]);
  });

  it("places overlapping parallel stacks in separate lanes at the same axial station", () => {
    const stack = (name: string, x: number) => [box(`${name}_shaft`, x, 0, 0, 10, 10, 300), box(`${name}_gear`, x, 0, 20, 100, 100, 20)];
    const parts = [...stack("a", 0), ...stack("b", 70), ...stack("c", 140)];
    const layout = planExplodeLayout(parts);
    expectClear(parts, layout);
    for (const name of ["a", "b", "c"]) expect(layout.offsets.get(`${name}_shaft`)![2]).toBe(0);
    const centers = ["a", "b", "c"].map((name, i) => [0, 70, 140][i] + layout.offsets.get(`${name}_shaft`)![0]);
    expect(centers).toEqual([...centers].sort((a, b) => a - b));
    expect(centers[1] - centers[0]).toBeGreaterThanOrEqual(100);
  });

  it("still pulls apart parts that were already clear", () => {
    const parts = [box("a", -100, 0, 0, 10, 10, 10), box("b", 100, 0, 0, 10, 10, 10)];
    const layout = planExplodeLayout(parts);
    expectClear(parts, layout);
    expect(layout.offsets.get("b")![0] - layout.offsets.get("a")![0]).toBeGreaterThan(0);
  });

  it("is deterministic and leaves input untouched", () => {
    const frozen = gearbox.map(part => Object.freeze({ id: part.id, min: Object.freeze([...part.min]), max: Object.freeze([...part.max]) })) as unknown as ExplodePart[];
    const before = JSON.stringify(frozen);
    const first = planExplodeLayout(Object.freeze([...frozen]));
    const shuffled = [...frozen].sort((a, b) => (a.id.length * 31 + a.id.charCodeAt(a.id.length - 1)) % 17 - (b.id.length * 31 + b.id.charCodeAt(b.id.length - 1)) % 17);
    const second = planExplodeLayout(shuffled);
    expect(JSON.stringify(frozen)).toBe(before);
    for (const part of frozen) expect(second.offsets.get(part.id)).toEqual(first.offsets.get(part.id));
    expect(second.axis).toBe(first.axis);
  });

  it.each([1e-6, 1e-3, 1e3, 1e6])("scales with the model at %s", scale => {
    const parts = gearbox.map(part => ({ id: part.id, min: part.min.map(x => x * scale), max: part.max.map(x => x * scale) })) as unknown as ExplodePart[];
    const base = planExplodeLayout(gearbox);
    const layout = planExplodeLayout(parts);
    expectClear(parts, layout);
    expect(layout.axis).toBe(base.axis);
    expect(layout.gap).toBeCloseTo(base.gap * scale, 5);
    expect(overallSize(parts, layout)[2] / scale).toBeCloseTo(overallSize(gearbox, base)[2], 3);
  });

  it("handles a tiny part inside a huge one, zero-size parts and flat models", () => {
    const extremes = [box("huge", 0, 0, 0, 1e6, 1e6, 1e6), box("tiny", 0, 0, 0, 1e-6, 1e-6, 1e-6), box("point", 10, 10, 10, 0, 0, 0)];
    expectClear(extremes, planExplodeLayout(extremes));
    const points = [box("a", 0, 0, 0, 0, 0, 0), box("b", 0, 0, 0, 0, 0, 0)];
    const atPoint = planExplodeLayout(points);
    expectClear(points, atPoint);
    expect(atPoint.gap).toBeGreaterThan(0);
    const flat = [box("plate", 0, 0, 0, 100, 100, 0), box("sticker", 5, 5, 0, 10, 10, 0)];
    expectClear(flat, planExplodeLayout(flat));
  });

  it("rejects bad bounds and duplicate ids", () => {
    expect(() => planExplodeLayout([box("a", 0, 0, 0, 1, 1, 1), box("a", 2, 2, 2, 1, 1, 1)])).toThrow("Duplicate");
    expect(() => planExplodeLayout([{ id: "a", min: [0, 0, NaN], max: [1, 1, 1] }])).toThrow("Invalid");
    expect(() => planExplodeLayout([{ id: "a", min: [2, 0, 0], max: [1, 1, 1] }])).toThrow("Invalid");
  });

  describe("large assemblies", () => {
    const random = (seed: number) => () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;

    it.each([
      ["scattered parts", 5000, 2000, 30],
      ["heavily overlapping parts", 2000, 200, 40],
    ])("lays out %s quickly", (_name, count, span, size) => {
      const next = random(7);
      const parts = Array.from({ length: count }, (_, i) => box(`p${i}`, next() * span, next() * span, next() * span, next() * size + 0.1, next() * size + 0.1, next() * size + 0.1));
      const start = performance.now();
      const layout = planExplodeLayout(parts);
      expect(performance.now() - start).toBeLessThan(5000);
      expectClear(parts, layout);
    });

    it("lays out many repeated fasteners around many shafts", () => {
      const parts: ExplodePart[] = [];
      for (let s = 0; s < 60; s++) {
        const x = (s % 10) * 150, y = Math.floor(s / 10) * 150;
        parts.push(box(`shaft${s}`, x, y, 0, 20, 20, 400));
        for (let b = 0; b < 40; b++) {
          const angle = (b / 40) * 2 * Math.PI;
          parts.push(box(`ball${s}_${b}`, x + 50 * Math.cos(angle), y + 50 * Math.sin(angle), 100, 4, 4, 4));
        }
      }
      const start = performance.now();
      const layout = planExplodeLayout(parts);
      expect(performance.now() - start).toBeLessThan(5000);
      expectClear(parts, layout);
    });
  });
});
