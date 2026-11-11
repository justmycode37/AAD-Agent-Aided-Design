// Thin wrapper over three-cad-viewer, so the rest of the app does not depend on its option shapes.
import { Display, Viewer, logger } from "three-cad-viewer";
import "three-cad-viewer/css";
import { displayStates, type DisplayMode, type Visibility } from "./display-mode";
import type { Analysis } from "./transport/types";
import { installNavigation } from "./navigation";
import { planExplodeLayout, type ExplodePart } from "./explode-layout";
import { cameraFit } from "./camera-fit";
import { installAdaptiveGrid } from "./adaptive-grid";

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
  setExploded(flag: boolean, animate?: boolean): void;
  isExploded(): boolean;
  setGrid(flag: boolean): void;
  canExplode(): boolean;
  fit(): void;
  batch(action: () => void): void;
  reset(): void;
  setAnalysis(analysis?: Analysis): void;
  readonly fullControls: boolean;
}

export function mountCad3d(
  host: HTMLElement,
  shapes: unknown,
  opts: { width: number; height: number; theme: "light" | "dark"; view?: string; controls?: "simple" | "cad" },
): Cad3dView {
  logger.setLevel("warn");
  const fullControls = opts.controls === "cad";
  const cadSize = (w: number, h: number) => ({
    cadWidth: Math.max(MIN_WIDTH, Math.floor(w) - 6 - (fullControls ? TREE_WIDTH : 0)),
    height: Math.max(MIN_HEIGHT, Math.floor(h) - (fullControls ? 96 : 4)),
  });
  const base = {
    ...cadSize(opts.width, opts.height),
    treeWidth: TREE_WIDTH,
    treeHeight: TREE_HEIGHT,
    theme: opts.theme,
    glass: !fullControls,
    tools: fullControls,
    pinning: true,
    measureTools: true,
    externalMeasurementBackend: false,
    selectTool: true,
    explodeTool: true, // Enable assembly animation; its native UI stays hidden.
    zebraTool: true,
    zscaleTool: true,
    studioTool: true,
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
    viewer.glassMode(!fullControls);
    if (opts.view) viewer.presetCamera(opts.view as never);
    viewer.setCameraZoom(FIT_ZOOM);
    const markerCamera = viewer.rendered.orientationMarker.camera;
    if (markerCamera) { markerCamera.zoom = 0.8; markerCamera.updateProjectionMatrix(); }
    viewer.showTools(fullControls);
    viewer.setPickHandler(fullControls);
    viewer.setSelectionInput(fullControls);
    const disposeNavigation = installNavigation(viewer as never);
    const disposeAdaptiveGrid = installAdaptiveGrid(viewer);
    // Keep keyboard navigation on the simple buttons. Hidden CAD actions must
    // not reappear when somebody presses a letter while turning the model.
    if (!fullControls) viewer.setKeyMap(Object.fromEntries([
      "axes", "axes0", "grid", "gridxy", "perspective", "transparent", "blackedges", "zscale", "reset", "resize", "iso", "front", "rear", "top", "bottom", "left", "right", "explode", "distance", "properties", "select", "help", "play", "stop", "tree", "clip", "material", "zebra", "studio",
    ].map(action => [action, ""])) as never);
    const camera = viewer.rendered.camera as any;
    const clipRanges = [camera.pCamera, camera.oCamera].map(c => ({ camera: c, far: c.far }));
    const restoreFarPlanes = () => {
      // Native CAD collapse can keep a zoomed-out camera. Cover the assembled
      // model at that distance until its framing is restored as well.
      const sphere = viewer.bbox!.boundingSphere();
      const needed = camera.getCamera().position.distanceTo(sphere.center) + 2 * sphere.radius;
      for (const range of clipRanges) { range.camera.far = Math.max(range.far, needed); range.camera.updateProjectionMatrix(); }
    };
    const extendFarPlanes = (bounds: any) => {
      if (bounds.isEmpty()) return;
      const sphere = bounds.boundingSphere();
      const far = Math.max(100 * sphere.radius, camera.getCamera().position.distanceTo(sphere.center) + 2 * sphere.radius);
      for (const range of clipRanges) {
        range.camera.far = Math.max(range.far, far);
        range.camera.updateProjectionMatrix();
      }
    };
    const unsubscribeExplode = viewer.state.subscribe("animationMode", change => {
      if (change.new !== "explode") restoreFarPlanes();
    });
    const nativeReset = viewer.reset;
    viewer.reset = () => {
      cancelExplodeTransition();
      nativeReset();
      // The engine resets its controls target but leaves the camera wrapper's
      // zoom origin at the last pan position.
      camera.target.copy(viewer.rendered.controls.getTarget());
      if (viewer.state.get("animationMode") !== "explode") restoreFarPlanes();
      fit();
    };
    const visibleKey = () => JSON.stringify(Object.entries(viewer.getStates()).filter(([, state]) => state.includes(1)).map(([id]) => id).sort());
    let plannedVisibility = "";
    let explodeSettings: [number, number, number] = [2, 1, 2.5];
    // Use the engine's animation lifecycle in both interfaces, with an axial
    // layout that also separates concentric parts instead of scaling centers.
    viewer.explode = (duration = 2, speed = 1, multiplier = 2.5) => {
      viewer.clearAnimation();
      explodeSettings = [duration, speed, multiplier];
      plannedVisibility = visibleKey();
      const groups = viewer.rendered.nestedGroup.groups;
      (viewer.rendered.nestedGroup.rootGroup as any).updateMatrixWorld(true);
      const parts: ExplodePart[] = [];
      for (const [id, state] of Object.entries(viewer.getStates())) {
        if (!state.includes(1) || !groups[id]) continue;
        const bounds = (viewer.bbox! as any).clone().makeEmpty().expandByObject(groups[id], false);
        if (!bounds.isEmpty()) parts.push({ id, min: bounds.min.toArray(), max: bounds.max.toArray() });
      }
      const strength = Math.max(0, multiplier / 2.5);
      const offsets = planExplodeLayout(parts).offsets;
      const explodedBounds = (viewer.bbox! as any).clone().makeEmpty();
      for (const part of parts) {
        const delta = (groups[part.id] as any).position.clone().fromArray(offsets.get(part.id)!).multiplyScalar(strength);
        explodedBounds.expandByPoint(delta.clone().fromArray(part.min).add(delta));
        explodedBounds.expandByPoint(delta.clone().fromArray(part.max).add(delta));
      }
      let tracks = 0;
      for (const id of Object.keys(viewer.getStates())) {
        const group = groups[id] as any;
        if (!group) continue;
        // Keep zero tracks for hidden leaves, so even hiding the last visible
        // piece keeps explode mode ready for a later tree visibility change.
        const offset = offsets.get(id) ?? [0, 0, 0];
        const delta = group.position.clone().fromArray(offset).multiplyScalar(strength);
        if (group.parent) {
          const origin = group.parent.worldToLocal(group.position.clone().set(0, 0, 0));
          group.parent.worldToLocal(delta).sub(origin);
        }
        viewer.addPositionTrack(id, [0, duration], [[0, 0, 0], delta.toArray()]);
        tracks++;
      }
      if (tracks) viewer.initAnimation(duration, speed, "E", false);
      extendFarPlanes(explodedBounds);
    };
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
    // Tree notifications cover native checkbox/group changes and both public
    // visibility setters, after the entire visibility change has completed.
    const tree = viewer.rendered.treeview as any;
    const notifyVisibility = tree.notificationHandler;
    tree.notificationHandler = () => {
      if (viewer.state.get("animationMode") === "explode" && visibleKey() !== plannedVisibility) {
        const time = viewer.getRelativeTime();
        const playing = (viewer as any).clipAction?.isRunning() && !(viewer as any).clipAction.paused;
        batch(() => {
          viewer.explode(...explodeSettings);
          viewer.setRelativeTime(time);
          if (playing) viewer.controlAnimation("play");
        });
      }
      notifyVisibility();
    };
    const initialStates = structuredClone(viewer.getStates());
    const painted: { mesh: any; material: any }[] = [];
    const paintMaterials = new Set<any>();
    const setAnalysis = (analysis?: Analysis) => {
      for (const { mesh, material } of painted) mesh.material = material;
      for (const material of paintMaterials) material.dispose();
      painted.length = 0; paintMaterials.clear();
      if (!analysis) return;
      const [low, high] = analysis.range;
      const palette = [0x315caa, 0x2aa89a, 0xe6cf53, 0xc84b4b];
      for (const row of analysis.values) {
        const group = viewer.rendered.nestedGroup.groups[row.part] as any; // The pinned d.ts omits inherited Three.js methods.
        if (!group) throw new Error(`Analysis part is missing: ${row.part}`);
        const t = high === low ? .5 : Math.max(0, Math.min(1, (row.value - low) / (high - low)));
        const p = t * 3, i = Math.min(2, Math.floor(p)), fraction = p - i;
        const rgb = [16, 8, 0].map(shift => Math.round(((palette[i] >> shift) & 255) * (1 - fraction) + ((palette[i + 1] >> shift) & 255) * fraction));
        const color = `#${rgb.map(n => n.toString(16).padStart(2, "0")).join("")}`;
        group.traverse((mesh: any) => {
          if (!mesh.isMesh || !mesh.material) return;
          const original = mesh.material;
          const clone = (material: any) => {
            const copy = material.clone();
            copy.color?.set(color); paintMaterials.add(copy); return copy;
          };
          mesh.material = Array.isArray(original) ? original.map(clone) : clone(original);
          painted.push({ mesh, material: original });
        });
      }
    };
    let assembledCamera = viewer.getCameraLocationSettings();
    let assembledWrapperTarget = viewer.rendered.camera.target.clone();
    let assembledViewport = lastSize;
    let explodeFrame = 0;
    let explodeGeneration = 0;
    let transitionTarget = viewer.state.get("animationMode") === "explode";
    const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    const cancelExplodeTransition = () => {
      explodeGeneration++;
      if (explodeFrame) cancelAnimationFrame(explodeFrame);
      explodeFrame = 0;
    };
    const cameraPose = () => ({
      settings: viewer.getCameraLocationSettings(),
      wrapperTarget: camera.target.clone(),
    });
    const applyCameraPose = (from: ReturnType<typeof cameraPose>, to: ReturnType<typeof cameraPose>, t: number) => {
      const mix = (a: number[], b: number[]) => a.map((value, i) => value + (b[i] - value) * t);
      camera.target.copy(from.wrapperTarget).lerp(to.wrapperTarget, t);
      const s = from.settings, e = to.settings;
      const quaternion = camera.getCamera().quaternion.clone().fromArray(s.quaternion)
        .slerp(camera.getCamera().quaternion.clone().fromArray(e.quaternion), t).toArray();
      viewer.setCameraLocationSettings(mix(s.position, e.position), quaternion,
        mix(s.target, e.target), s.zoom + (e.zoom - s.zoom) * t, false);
    };
    const fit = () => {
      const bounds = (viewer.bbox! as any).clone().makeEmpty();
      for (const [id, state] of Object.entries(viewer.getStates())) {
        const group = viewer.rendered.nestedGroup.groups[id];
        if (state.includes(1) && group) bounds.expandByObject(group, false);
      }
      if (bounds.isEmpty()) return;
      const center = bounds.center();
      viewer.setCameraTarget(center);
      camera.target.fromArray(center);
      const c = camera.getCamera();
      const inverse = c.quaternion.clone().invert();
      const points = [bounds.min.x, bounds.max.x].flatMap(x => [bounds.min.y, bounds.max.y].flatMap(y => [bounds.min.z, bounds.max.z].map(z =>
        c.position.clone().set(x, y, z).sub(camera.target).applyQuaternion(inverse).toArray())));
      const canvas = viewer.rendered.controls.domElement;
      const fitted = cameraFit(points, { width: canvas.clientWidth, height: canvas.clientHeight,
        scaleX: c.projectionMatrix.elements[0], scaleY: c.projectionMatrix.elements[5], ortho: !!c.isOrthographicCamera,
        distance: c.position.distanceTo(camera.target), near: c.near, verticalPadding: fullControls ? 24 : 56 });
      const zoom = viewer.getCameraZoom();
      c.position.sub(camera.target).setLength(fitted.distance).add(camera.target);
      viewer.setCameraZoom(c.isOrthographicCamera ? zoom * fitted.zoomFactor : camera.camera_distance / fitted.distance);
      extendFarPlanes(bounds);
    };
    const setExploded = (flag: boolean, animate = true) => {
      const currentlyExploded = viewer.state.get("animationMode") === "explode";
      if (flag === currentlyExploded && !explodeFrame) return;
      cancelExplodeTransition();
      transitionTarget = flag;
      if (flag) {
        if (!currentlyExploded) {
          assembledCamera = viewer.getCameraLocationSettings();
          assembledWrapperTarget = viewer.rendered.camera.target.clone();
          assembledViewport = lastSize;
          viewer.setExplode(true);
        }
      } else {
        if (!currentlyExploded) return;
      }
      const startTime = viewer.getRelativeTime();
      const startCamera = cameraPose();
      const destinationTime = flag ? 1 : 0;
      let endCamera!: ReturnType<typeof cameraPose>;
      batch(() => {
        if (flag) {
          viewer.setRelativeTime(1); fit();
          endCamera = cameraPose();
        } else {
          viewer.setRelativeTime(0);
          viewer.rendered.camera.target.copy(assembledWrapperTarget);
          viewer.setCameraLocationSettings(assembledCamera.position, assembledCamera.quaternion, assembledCamera.target, assembledCamera.zoom);
          endCamera = cameraPose();
          if (lastSize !== assembledViewport) { fit(); endCamera = cameraPose(); }
        }
        viewer.setRelativeTime(startTime);
        applyCameraPose(startCamera, endCamera, 0); // preserve the visible starting pose
      });
      if (!animate || reducedMotion() || startTime === destinationTime) {
        viewer.setRelativeTime(destinationTime);
        applyCameraPose(startCamera, endCamera, 1);
        if (!flag) {
          viewer.setExplode(false);
          for (const range of clipRanges) { range.camera.far = range.far; range.camera.updateProjectionMatrix(); }
        }
        viewer.update(true, false);
        return;
      }
      const generation = explodeGeneration;
      const duration = Math.max(100, 420 * Math.abs(destinationTime - startTime));
      const started = performance.now();
      const tick = (now: number) => {
        if (generation !== explodeGeneration) return;
        const t = Math.min(1, (now - started) / duration);
        const eased = t * t * (3 - 2 * t);
        batch(() => {
          viewer.setRelativeTime(startTime + (destinationTime - startTime) * eased);
          applyCameraPose(startCamera, endCamera, eased);
        });
        if (t < 1) explodeFrame = requestAnimationFrame(tick);
        else {
          explodeFrame = 0;
          if (!flag) {
            viewer.setExplode(false);
            for (const range of clipRanges) { range.camera.far = range.far; range.camera.updateProjectionMatrix(); }
          }
        }
      };
      explodeFrame = requestAnimationFrame(tick);
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
      fullControls,
      setAnalysis,
      setDisplayMode,
      setExploded,
      isExploded() { return explodeFrame ? transitionTarget : viewer.state.get("animationMode") === "explode"; },
      batch,
      fit,
      setGrid(flag) { viewer.setGrids([flag, flag, flag]); viewer.setAxes(flag); },
      canExplode() { return Object.values(viewer.getStates()).filter(state => state.includes(1)).length > 1; },
      showAll() { viewer.setStates(displayStates(initialStates, mode) as never); },
      hidePart(id) { viewer.setState(id, [0, 0]); },
      isolatePart(id) { isolateParts([id]); },
      isolateParts,
      reset() {
        setAnalysis();
        setExploded(false, false);
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
        batch(() => { viewer.resizeCadView(s.cadWidth, TREE_WIDTH, s.height, !fullControls); fit(); });
      },
      setTheme(theme) {
        display.setTheme(theme);
        const camera = viewer.rendered.orientationMarker.camera;
        if (camera) { camera.zoom = 0.8; camera.updateProjectionMatrix(); }
      },
      dispose() {
        cancelExplodeTransition();
        disposeNavigation();
        disposeAdaptiveGrid();
        unsubscribeExplode();
        setAnalysis();
        viewer.dispose();
      },
    };
    view.resize(opts.width, opts.height);
    return view;
  } finally { viewer.renderer.render = initialRender; }
}
