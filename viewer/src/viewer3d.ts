// Thin wrapper over three-cad-viewer, so the rest of the app does not depend on its option shapes.
import { Display, Viewer, logger } from "three-cad-viewer";
import "three-cad-viewer/css";
import { displayStates, type DisplayMode, type Visibility } from "./display-mode";

const TREE_WIDTH = 220;
const TREE_HEIGHT = 320;
const MIN_WIDTH = 240;
const MIN_HEIGHT = 240;
/** three-cad-viewer's default framing crops parts in a short chat frame. */
const FIT_ZOOM = 0.72;

export interface Cad3dView {
  resize(width: number, height: number): void;
  setTheme(theme: "light" | "dark"): void;
  dispose(): void;
  readonly viewer: Viewer;
  readonly display: Display;
  setDisplayMode(mode: DisplayMode): void;
  showAll(): void;
  hidePart(id: string): void;
  isolatePart(id: string): void;
  isolateParts(ids: string[]): void;
  setExploded(flag: boolean): void;
  setGrid(flag: boolean): void;
  canExplode(): boolean;
  fit(): void;
  batch(action: () => void): void;
  reset(): void;
}

export function mountCad3d(
  host: HTMLElement,
  shapes: unknown,
  opts: { width: number; height: number; theme: "light" | "dark"; view?: string },
): Cad3dView {
  logger.setLevel("warn");
  const cadSize = (w: number, h: number) => ({
    cadWidth: Math.max(MIN_WIDTH, Math.floor(w) - 6),
    height: Math.max(MIN_HEIGHT, Math.floor(h) - 4),
  });
  const base = {
    ...cadSize(opts.width, opts.height),
    treeWidth: TREE_WIDTH,
    treeHeight: TREE_HEIGHT,
    theme: opts.theme,
    glass: true,
    tools: false,
    pinning: false,
    measureTools: false,
    externalMeasurementBackend: false,
    selectTool: false,
    explodeTool: true, // Enable assembly animation; its native UI stays hidden.
    zebraTool: false,
    zscaleTool: false,
    studioTool: false,
  };
  const display = new Display(host, base);
  const viewer = new Viewer(display, base, (() => {}) as never);
  // Build the original scene and camera before the first draw. The app applies
  // the requested part filter immediately after mount, so hidden housings need
  // not be drawn once just to be hidden again.
  const initialRender = viewer.renderer.render;
  viewer.renderer.render = () => {};
  try {
    viewer.render(
      shapes as never,
      { ambientIntensity: 1, directIntensity: 1.1, metalness: 0.3, roughness: 0.65, edgeColor: 0x303030, defaultOpacity: 0.5, normalLen: 0 } as never,
      { up: "Z", ortho: false, axes: false, grid: [false, false, false] } as never,
    );
    viewer.glassMode(true);
    if (opts.view) viewer.presetCamera(opts.view as never);
    viewer.setCameraZoom(FIT_ZOOM);
    const markerCamera = viewer.rendered.orientationMarker.camera;
    if (markerCamera) { markerCamera.zoom = 0.8; markerCamera.updateProjectionMatrix(); }
    viewer.showTools(false);
    viewer.setPickHandler(false);
    viewer.setSelectionInput(false);
    // Keep keyboard navigation on the simple buttons. Hidden CAD actions must
    // not reappear when somebody presses a letter while turning the model.
    viewer.setKeyMap(Object.fromEntries([
      "axes", "axes0", "grid", "gridxy", "perspective", "transparent", "blackedges", "zscale", "reset", "resize", "iso", "front", "rear", "top", "bottom", "left", "right", "explode", "distance", "properties", "select", "help", "play", "stop", "tree", "clip", "material", "zebra", "studio",
    ].map(action => [action, ""])) as never);
    let mode: DisplayMode = "shaded-edges";
    let lastSize = cadSize(opts.width, opts.height);
    let batchDepth = 0;
    const batch = (action: () => void) => {
      if (batchDepth) { action(); return; }
      const render = viewer.renderer.render;
      // CAD setters draw immediately. Prepare a complete view before drawing,
      // rather than queueing many full assembly frames for one user action.
      viewer.renderer.render = () => {};
      batchDepth++;
      try { action(); } finally { batchDepth--; viewer.renderer.render = render; viewer.update(true, false); }
    };
    const initialStates = structuredClone(viewer.getStates());
    let assembledCamera = viewer.getCameraLocationSettings();
    const fit = () => {
      const bounds = (viewer.bbox! as any).clone().makeEmpty();
      for (const [id, state] of Object.entries(viewer.getStates())) {
        const group = viewer.rendered.nestedGroup.groups[id];
        if (state.includes(1) && group) bounds.expandByObject(group, false);
      }
      if (bounds.isEmpty()) return;
      viewer.setCameraTarget(bounds.center());
      viewer.setCameraZoom(FIT_ZOOM * viewer.bbox!.boundingSphere().radius / Math.max(1e-9, bounds.boundingSphere().radius));
    };
    const setExploded = (flag: boolean) => {
      if (flag === (viewer.state.get("animationMode") === "explode")) return;
      if (flag) {
        assembledCamera = viewer.getCameraLocationSettings();
        viewer.setExplode(true); viewer.setRelativeTime(1);
        fit();
      } else {
        viewer.setExplode(false);
        viewer.setCameraLocationSettings(assembledCamera.position, assembledCamera.quaternion, assembledCamera.target, assembledCamera.zoom);
      }
    };
    const isolateParts = (ids: string[]) => {
      const selected = new Set(ids);
      const states = Object.fromEntries(Object.keys(initialStates).map(path => [path, selected.has(path) ? initialStates[path] : [0, 0] as Visibility]));
      viewer.setStates(displayStates(states, mode) as never);
    };
    const setDisplayMode = (next: DisplayMode) => {
      mode = next;
      viewer.setStates(displayStates(viewer.getStates(), mode) as never);
      viewer.setTransparent(mode === "transparent");
    };

    const view: Cad3dView = {
      viewer,
      display,
      setDisplayMode,
      setExploded,
      batch,
      fit,
      setGrid(flag) { viewer.setGrids([flag, flag, flag]); viewer.setAxes(flag); },
      canExplode() { return Object.values(viewer.getStates()).filter(state => state.includes(1)).length > 1; },
      showAll() { viewer.setStates(displayStates(initialStates, mode) as never); },
      hidePart(id) { viewer.setState(id, [0, 0]); },
      isolatePart(id) { isolateParts([id]); },
      isolateParts,
      reset() {
        setExploded(false);
        viewer.resetClip();
        viewer.setActiveTab("tree");
        for (const tool of ["distance", "properties", "select"]) viewer.activateTool(tool, false);
        mode = "shaded-edges";
        viewer.setStates(initialStates);
        viewer.setTransparent(false);
        viewer.setOrtho(false);
        viewer.setAxes(false); viewer.setGrids([false, false, false]);
        display.reset();
        viewer.presetCamera("iso");
        viewer.setCameraZoom(FIT_ZOOM);
      },
      resize(width, height) {
        const s = cadSize(width, height);
        if (s.cadWidth === lastSize.cadWidth && s.height === lastSize.height) return;
        lastSize = s;
        viewer.resizeCadView(s.cadWidth, TREE_WIDTH, s.height, true);
      },
      setTheme(theme) {
        display.setTheme(theme);
        const camera = viewer.rendered.orientationMarker.camera;
        if (camera) { camera.zoom = 0.8; camera.updateProjectionMatrix(); }
      },
      dispose() {
        viewer.dispose();
      },
    };
    view.resize(opts.width, opts.height);
    return view;
  } finally { viewer.renderer.render = initialRender; }
}
