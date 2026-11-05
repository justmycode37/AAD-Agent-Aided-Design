import { describe, expect, it } from "vitest";
import { partDraft, partReference, partReferenceUri, partSelectionContext } from "../src/part-reference";
import type { SceneManifest } from "../src/transport/types";

const scene = { project: "gearbox", rev: 7, scene_id: "a".repeat(64), parts: [
  { name: "screw", path: "/assembly/left/screw" },
  { name: "screw", path: "/assembly/right/screw" },
  { name: "Gehäuse", path: "/assembly/case?bolt#1" },
] } as SceneManifest;

describe("part references", () => {
  it("distinguishes repeated part names by exact path", () => {
    const left = partReference(scene, scene.parts[0].path), right = partReference(scene, scene.parts[1].path);
    expect(left.mention).not.toBe(right.mention);
    expect(left.path).toBe(scene.parts[0].path);
    expect(partReference({ ...scene, parts: [...scene.parts].reverse() }, left.path).mention).toBe(left.mention);
  });
  it("uses only a displayed part path, never an ambiguous name", () => {
    expect(() => partReference(scene, "screw")).toThrow();
    expect(() => partReference(scene, "/other-scene/screw")).toThrow();
  });
  it("preserves revision, hash and encoded special characters through copied text", () => {
    const ref = partReference(scene, scene.parts[2].path), uri = new URL(partReferenceUri(ref));
    expect(uri.protocol).toBe("aad:");
    expect(uri.hostname).toBe("part");
    expect(uri.pathname).toBe(`/gearbox/7/${scene.scene_id}`);
    expect(uri.searchParams.get("path")).toBe(ref.path);
    expect(partDraft(ref, "  change its thickness  ")).toBe(`${ref.mention} change its thickness\n\nAAD part: ${uri.href}`);
  });
  it("keeps separate source revisions distinguishable", () => {
    const a = partReference(scene, scene.parts[0].path);
    const b = partReference({ ...scene, rev: 8, scene_id: "b".repeat(64) }, a.path);
    expect(partReferenceUri(a)).not.toBe(partReferenceUri(b));
  });
  it("disambiguates names that become the same mention after normalization", () => {
    const parts = [{ name: "gear wheel", path: "/a" }, { name: "gear/wheel", path: "/b" }];
    expect(partReference({ ...scene, parts }, "/a").mention).not.toBe(partReference({ ...scene, parts }, "/b").mention);
  });
  it("bounds host context and reports omitted references instead of silently dropping them", () => {
    const first = partReference(scene, scene.parts[0].path);
    const refs = Array.from({ length: 200 }, (_, i) => ({ ...first, path: `/group/${i}`, mention: `@screw_${i}` }));
    const context = partSelectionContext(refs, { text: {}, structuredContent: {} });
    expect(new TextEncoder().encode(JSON.stringify(context)).byteLength).toBeLessThanOrEqual(12000);
    expect(context.structuredContent!.omittedCount).toBeGreaterThan(0);
    expect(context.structuredContent!.selectedCount).toBe(200);
    expect(context.content![0].text).toContain("before acting");
  });
  it("sends only advertised context modalities and clears previous selections", () => {
    expect(partSelectionContext([], { structuredContent: {} })).toEqual({ structuredContent: { selection: [], selectedCount: 0, omittedCount: 0 } });
    expect(partSelectionContext([], { text: {} }).structuredContent).toBeUndefined();
  });
});
