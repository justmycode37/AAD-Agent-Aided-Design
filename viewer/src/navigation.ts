// Fusion-style trackpad navigation layered on the CAD engine's own controls.
// On a Mac, two-finger scroll pans and pinch zooms. Every other input keeps the
// engine's behavior: click-drag orbit, Shift/right-drag pan, and native wheel zoom.

interface Vec3 { x: number; y: number; z: number }

/** The parts of the pinned engine navigation uses. The engine keeps two lookAt targets: see `pan`. */
export interface NavigationViewer {
  update(updateMarker: boolean, notify?: boolean): void;
  rendered: {
    camera: {
      target: Vec3;
      camera_distance: number;
      getCamera(): {
        position: Vec3;
        zoom: number;
        fov?: number;
        top?: number;
        bottom?: number;
        isOrthographicCamera?: boolean;
        matrixWorld: { elements: ArrayLike<number> };
        updateMatrixWorld(): void;
        updateProjectionMatrix(): void;
      };
    };
    controls: {
      domElement: HTMLElement;
      controls: { enabled: boolean; addEventListener(type: "change", listener: () => void): void; removeEventListener(type: "change", listener: () => void): void };
      getTarget(): Vec3;
      update(): void;
    };
  } | null | undefined;
}

export type WheelAction = "pan" | "zoom" | "native";

const WHEEL_DELTA_PIXEL = 0;

/** Multiplying by exp(-deltaY / 100) inverts how Chromium encodes a pinch scale as a ctrl-wheel delta. */
const PINCH_DELTA_PER_E = 100;
/** Keeps one notch of a physical Ctrl-wheel from jumping; a pinch event stays well below this. */
const MAX_PINCH_DELTA = 25;
const MIN_ZOOM = 0.02;
const MAX_ZOOM = 100;

/** macOS only: iPadOS also reports a Mac platform, but it pinches through touch, which the engine handles. */
export function isMacPlatform(nav: Pick<Navigator, "platform" | "maxTouchPoints"> = navigator): boolean {
  return /^Mac/i.test(nav.platform ?? "") && !(nav.maxTouchPoints > 1);
}

export function navigationHint(mac = isMacPlatform()): string {
  return mac
    ? "Drag to turn · Two-finger scroll to pan · Pinch or Option-scroll to zoom"
    : "Drag to turn · Right-drag to pan · Scroll or pinch to zoom";
}

/** Decides by input kind, not delta size, because a trackpad and a mouse wheel can send identical deltas. */
export function classifyWheel(event: Pick<WheelEvent, "ctrlKey" | "altKey" | "buttons" | "deltaMode">, mac: boolean): WheelAction {
  if (event.buttons) return "native"; // The engine owns the wheel during a drag.
  if (event.ctrlKey) return "zoom"; // Browsers report a trackpad pinch as ctrl-wheel.
  return mac && !event.altKey && event.deltaMode === WHEEL_DELTA_PIXEL ? "pan" : "native";
}

function clampZoom(current: number, next: number): number {
  // Native wheel zoom has no limits, so never snap a view already outside this range.
  return Math.min(Math.max(next, Math.min(current, MIN_ZOOM)), Math.max(current, MAX_ZOOM));
}

/** Moves the view in the screen plane so content follows the fingers at 1:1 at the target's depth. */
function pan(rendered: NonNullable<NavigationViewer["rendered"]>, cssHeight: number, dx: number, dy: number): void {
  if (cssHeight <= 0) return;
  const camera = rendered.camera.getCamera();
  const target = rendered.controls.getTarget();
  const { position } = camera;
  const visibleHeight = camera.isOrthographicCamera
    ? (camera.top! - camera.bottom!) / camera.zoom
    : 2 * Math.tan((camera.fov! * Math.PI) / 360) * Math.hypot(position.x - target.x, position.y - target.y, position.z - target.z) / camera.zoom;
  const unitsPerPixel = visibleHeight / cssHeight;
  camera.updateMatrixWorld();
  const m = camera.matrixWorld.elements; // Columns 0 and 1 are the camera's right and up axes.
  const right = dx * unitsPerPixel, up = -dy * unitsPerPixel;
  const shift = { x: m[0]! * right + m[4]! * up, y: m[1]! * right + m[5]! * up, z: m[2]! * right + m[6]! * up };
  // Controls orbit around their target; the camera wrapper measures perspective zoom from its own.
  // Moving only one would change the reported zoom and make the next zoom setter swing the view.
  for (const point of new Set<Vec3>([position, target, rendered.camera.target])) {
    point.x += shift.x; point.y += shift.y; point.z += shift.z;
  }
}

/** Zooms about the orbit target without moving it, so a pinch cannot pan. */
function zoomBy(rendered: NonNullable<NavigationViewer["rendered"]>, factor: number): void {
  const camera = rendered.camera.getCamera();
  if (camera.isOrthographicCamera) {
    camera.zoom = clampZoom(camera.zoom, camera.zoom * factor);
    camera.updateProjectionMatrix();
    return;
  }
  const { position } = camera;
  const target = rendered.controls.getTarget();
  const distance = Math.hypot(position.x - target.x, position.y - target.y, position.z - target.z);
  const reference = rendered.camera.camera_distance;
  if (distance <= 0 || reference <= 0) return;
  const zoom = reference / distance;
  const ratio = zoom / clampZoom(zoom, zoom * factor);
  position.x = target.x + (position.x - target.x) * ratio;
  position.y = target.y + (position.y - target.y) * ratio;
  position.z = target.z + (position.z - target.z) * ratio;
}

/**
 * Adds trackpad navigation to the canvas the engine's controls listen on.
 * @returns a function that removes it. Call it before disposing the viewer.
 */
export function installNavigation(viewer: NavigationViewer): () => void {
  const canvas = viewer.rendered!.controls.domElement;
  let dx = 0, dy = 0, zoom = 1, frame = 0, disposed = false;
  let gestureScale: number | null = null;

  const flush = () => {
    frame = 0;
    const rendered = viewer.rendered;
    const [panX, panY, factor] = [dx, dy, zoom];
    dx = dy = 0; zoom = 1;
    if (!rendered) return;
    if (panX || panY) pan(rendered, canvas.clientHeight, panX, panY);
    if (factor !== 1) zoomBy(rendered, factor);
    // The engine paints on its "change" event, which small moves and orthographic zoom do not fire.
    let painted = false;
    const markPainted = () => { painted = true; };
    const native = rendered.controls.controls;
    native.addEventListener("change", markPainted);
    rendered.controls.update();
    native.removeEventListener("change", markPainted);
    if (!painted) viewer.update(true, true);
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(flush); };

  const onWheel = (event: WheelEvent) => {
    if (viewer.rendered?.controls.controls.enabled === false) return;
    const action = classifyWheel(event, isMacPlatform());
    if (action === "native") return;
    // Listening in capture runs this before the engine's listeners on the same canvas.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (action === "pan") {
      dx += event.deltaX; dy += event.deltaY;
    } else if (gestureScale === null) { // A Safari gesture already applies this pinch.
      const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1);
      zoom *= Math.exp(-Math.max(-MAX_PINCH_DELTA, Math.min(MAX_PINCH_DELTA, pixels)) / PINCH_DELTA_PER_E);
    }
    schedule();
  };

  // Desktop Safari reports a trackpad pinch only as these events, with a scale relative to the gesture's start.
  const onGesture = (event: Event) => {
    if (!isMacPlatform() || viewer.rendered?.controls.controls.enabled === false) return;
    event.preventDefault();
    if (event.type === "gestureend") { gestureScale = null; return; }
    const scale = (event as Event & { scale?: number }).scale ?? 1;
    if (event.type === "gesturestart") { gestureScale = scale; return; }
    if (gestureScale && scale > 0) { zoom *= scale / gestureScale; gestureScale = scale; schedule(); }
  };

  const gestureEvents = ["gesturestart", "gesturechange", "gestureend"];
  const listenerOptions = { capture: true, passive: false };
  canvas.addEventListener("wheel", onWheel, listenerOptions);
  for (const type of gestureEvents) canvas.addEventListener(type, onGesture, listenerOptions);

  return () => {
    if (disposed) return;
    disposed = true;
    canvas.removeEventListener("wheel", onWheel, listenerOptions);
    for (const type of gestureEvents) canvas.removeEventListener(type, onGesture, listenerOptions);
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
  };
}
