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
    tools: true,
    pinning: false,
    measureTools: true,
    externalMeasurementBackend: false,
    selectTool: true, // Local inspection only; never calls a modeling tool.
    explodeTool: true,
    zebraTool: true,
    zscaleTool: false,
    studioTool: true,
  };
  const display = new Display(host, base);
  const viewer = new Viewer(display, base, (() => {}) as never);
  viewer.render(
    shapes as never,
    { ambientIntensity: 1, directIntensity: 1.1, metalness: 0.3, roughness: 0.65, edgeColor: 0x303030, defaultOpacity: 0.5, normalLen: 0 } as never,
    { up: "Z", ortho: false } as never,
  );
  viewer.glassMode(true);
  if (opts.view) viewer.presetCamera(opts.view as never);
  viewer.setCameraZoom(FIT_ZOOM);
  let mode: DisplayMode = "shaded-edges";
  const initialStates = structuredClone(viewer.getStates());
  const setDisplayMode = (next: DisplayMode) => {
    mode = next;
    viewer.setStates(displayStates(viewer.getStates(), mode) as never);
    viewer.setTransparent(mode === "transparent");
  };

  const view: Cad3dView = {
    viewer,
    display,
    setDisplayMode,
    showAll() { viewer.setStates(displayStates(initialStates, mode) as never); },
    hidePart(id) { viewer.setState(id, [0, 0]); },
    isolatePart(id) {
      const states = Object.fromEntries(Object.keys(initialStates).map((path) => [path, path === id ? initialStates[path] : [0, 0] as Visibility]));
      viewer.setStates(displayStates(states, mode) as never);
    },
    reset() {
      viewer.setExplode(false);
      viewer.resetClip();
      viewer.setActiveTab("tree");
      for (const tool of ["distance", "properties", "select"]) viewer.activateTool(tool, false);
      mode = "shaded-edges";
      viewer.setStates(initialStates);
      viewer.setTransparent(false);
      viewer.setOrtho(false);
      display.reset();
      viewer.presetCamera("iso");
      viewer.setCameraZoom(FIT_ZOOM);
    },
    resize(width, height) {
      const s = cadSize(width, height);
      viewer.resizeCadView(s.cadWidth, TREE_WIDTH, s.height, true);
    },
    setTheme(theme) {
      display.setTheme(theme);
    },
    dispose() {
      viewer.dispose();
    },
  };
  view.resize(opts.width, opts.height);
  return view;
}
