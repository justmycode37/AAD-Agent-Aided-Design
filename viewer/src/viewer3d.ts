// Thin wrapper over three-cad-viewer, so the rest of the app does not depend on its option shapes.
import { Display, Viewer, logger } from "three-cad-viewer";
import "three-cad-viewer/css";

const TREE_WIDTH = 220;
const TREE_HEIGHT = 200;
const MIN_WIDTH = 320;
const MIN_HEIGHT = 240;

export interface Cad3dView {
  resize(width: number, height: number): void;
  setTheme(theme: "light" | "dark"): void;
  dispose(): void;
  readonly viewer: Viewer;
}

export function mountCad3d(
  host: HTMLElement,
  shapes: unknown,
  opts: { width: number; height: number; theme: "light" | "dark"; onPick?: (pick: unknown) => void },
): Cad3dView {
  logger.setLevel("warn");
  const cadSize = (w: number, h: number) => ({
    cadWidth: Math.max(MIN_WIDTH, Math.floor(w) - 2),
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
    selectTool: true,
    explodeTool: true,
    zebraTool: true,
    zscaleTool: false,
    studioTool: true,
  };
  const display = new Display(host, base);
  const notify = (change: { lastPick?: { new?: unknown } }) => {
    const pick = change?.lastPick?.new;
    if (pick) opts.onPick?.(pick);
  };
  const viewer = new Viewer(display, base, notify as never);
  viewer.render(
    shapes as never,
    { ambientIntensity: 1, directIntensity: 1.1, metalness: 0.3, roughness: 0.65, edgeColor: 0x303030, defaultOpacity: 0.5, normalLen: 0 } as never,
    { up: "Z", ortho: false } as never,
  );
  viewer.glassMode(true);

  const view: Cad3dView = {
    viewer,
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
