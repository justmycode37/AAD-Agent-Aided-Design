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
  if (presentation.isolate || presentation.hide) view.fit();
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
  view.setExploded((presentation.explode ?? wasExploded) && view.canExplode());
  viewer.showTools(view.fullControls); viewer.setPickHandler(view.fullControls);
}
