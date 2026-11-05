import type { Cad3dView } from "./viewer3d";
import type { Presentation } from "./transport/types";

export function resolveParts(selectors: string[], ids: string[]): string[] {
  return [...new Set(selectors.flatMap(selector => {
    const matches = ids.filter(id => selector.startsWith("/")
      ? id === selector || id.startsWith(selector.replace(/\/$/, "") + "/")
      : id.split("/").at(-1) === selector);
    if (!matches.length) throw new Error(`Unknown part ${selector}`);
    if (!selector.startsWith("/") && matches.length > 1) throw new Error(`Use a full path for ${selector}`);
    return matches;
  }))];
}

/** Agent requests affect presentation only. They never change scene buffers. */
export function applyPresentation(view: Cad3dView, presentation: Presentation = {}): void {
  const viewer = view.viewer;
  const wasExploded = viewer.state.get("animationMode") === "explode";
  view.setExploded(false);
  view.showAll();
  viewer.resetClip(); viewer.setActiveTab("tree");
  view.setDisplayMode(presentation.style ?? "shaded-edges");
  const ids = Object.keys(viewer.getStates());
  if (presentation.isolate) view.isolateParts(resolveParts(presentation.isolate, ids));
  if (presentation.hide) for (const id of resolveParts(presentation.hide, ids)) view.hidePart(id);
  const materialKey = view as unknown as object;
  let records = paintedMaterials.get(materialKey);
  if (!records) { records = []; paintedMaterials.set(materialKey, records); }
  for (const record of records) record.mesh.material = record.original;
  records.length = 0;
  for (const override of presentation.materials ?? []) for (const id of resolveParts(override.parts, ids)) {
    const group = viewer.rendered.nestedGroup.groups[id] as any;
    group?.traverse((mesh: any) => {
      if (!mesh.isMesh || !mesh.material) return;
      const original = mesh.material;
      const copy = (material: any) => {
        const next = material.clone();
        if (override.color) next.color?.set(override.color);
        if (override.metalness !== undefined && "metalness" in next) next.metalness = override.metalness;
        if (override.roughness !== undefined && "roughness" in next) next.roughness = override.roughness;
        if (override.opacity !== undefined) { next.opacity = override.opacity; next.transparent = override.opacity < 1; next.depthWrite = override.opacity >= 1; }
        next.needsUpdate = true;
        return next;
      };
      mesh.material = Array.isArray(original) ? original.map(copy) : copy(original);
      records!.push({ mesh, original });
    });
  }
  viewer.setOrtho(presentation.projection === "orthographic");
  if (presentation.section) {
    viewer.setActiveTab("clip");
    viewer.setClipIntersection(presentation.section.intersection ?? false);
    const normals: Record<string, [number, number, number]> = { XY: [0, 0, -1], XZ: [0, -1, 0], YZ: [-1, 0, 0] };
    presentation.section.planes.forEach((plane, index) => {
      const raw = plane.normal ?? normals[plane.plane ?? "YZ"];
      const length = Math.hypot(...raw);
      if (!length || !Number.isFinite(length)) throw new Error("Invalid section normal");
      const normal = raw.map(n => n / length * (plane.flipped ? -1 : 1)) as [number, number, number];
      viewer.setClipNormal(index as 0 | 1 | 2, normal, 0);
      // refreshPlane supports -1 as an actual offset; setClipSlider reserves it.
      viewer.refreshPlane(index as 0 | 1 | 2, plane.offset ?? 0);
    });
  }
  if (presentation.grid !== undefined) view.setGrid(presentation.grid);
  // Exploded recipes must save an already fitted assembled camera, including
  // the requested projection, so Put together restores the same safe framing.
  view.fit();
  view.setExploded((presentation.explode ?? wasExploded) && view.canExplode());
  viewer.showTools(view.fullControls); viewer.setPickHandler(view.fullControls);
}

const paintedMaterials = new WeakMap<object, { mesh: any; original: any }[]>();
