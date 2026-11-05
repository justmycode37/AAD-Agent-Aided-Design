import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { classifyWheel, installNavigation, isMacPlatform, navigationHint, type NavigationViewer } from "../src/navigation";

const CSS_HEIGHT = 400;

function makeViewer(opts: { ortho?: boolean; sharedTarget?: boolean } = {}) {
  const controlsTarget = { x: 1, y: 2, z: 3 };
  const camera = {
    position: { x: 1, y: 2, z: 103 }, // Looks down -Z, so camera right is +X and up is +Y.
    zoom: 1,
    fov: 22,
    top: 50,
    bottom: -50,
    isOrthographicCamera: !!opts.ortho,
    matrixWorld: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
    updateMatrixWorld: vi.fn(),
    updateProjectionMatrix: vi.fn(),
  };
  const canvas = Object.assign(new EventTarget(), { clientHeight: CSS_HEIGHT }) as unknown as HTMLElement;
  const native = { enabled: true, addEventListener: vi.fn(), removeEventListener: vi.fn() };
  const wrapper = { target: opts.sharedTarget ? controlsTarget : { ...controlsTarget }, camera_distance: 200, getCamera: () => camera };
  const controls = { domElement: canvas, controls: native, getTarget: () => controlsTarget, update: vi.fn() };
  const viewer = { update: vi.fn(), rendered: { camera: wrapper, controls } } satisfies NavigationViewer;
  const nativeWheel = vi.fn(); // Stands in for the engine's listener: added after navigation, in the same phase order.
  return { viewer, camera, canvas, controls, wrapper, native, controlsTarget, nativeWheel };
}

function wheel(canvas: HTMLElement, init: Partial<WheelEvent>) {
  const event = Object.assign(new Event("wheel", { cancelable: true }), { deltaX: 0, deltaY: 0, deltaMode: 0, ctrlKey: false, altKey: false, buttons: 0, ...init });
  canvas.dispatchEvent(event);
  return event;
}

const setPlatform = (platform: string, maxTouchPoints = 0) => vi.stubGlobal("navigator", { platform, maxTouchPoints });

let frames: FrameRequestCallback[] = [];
const runFrame = () => { const queued = frames; frames = []; queued.forEach(cb => cb(0)); return queued.length; };

beforeEach(() => {
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames[id - 1] = () => {}; });
  setPlatform("MacIntel");
});
afterEach(() => vi.unstubAllGlobals());

describe("platform and input classification", () => {
  it("detects macOS, not iPadOS or other platforms", () => {
    expect(isMacPlatform({ platform: "MacIntel", maxTouchPoints: 0 })).toBe(true);
    expect(isMacPlatform({ platform: "MacIntel", maxTouchPoints: 5 })).toBe(false);
    expect(isMacPlatform({ platform: "Win32", maxTouchPoints: 0 })).toBe(false);
    expect(isMacPlatform({ platform: "Linux x86_64", maxTouchPoints: 0 })).toBe(false);
  });
  it("gives each platform its own hint", () => {
    expect(navigationHint(true)).toContain("Two-finger scroll to pan");
    expect(navigationHint(false)).toContain("Scroll or pinch to zoom");
  });
  it.each([
    [{ ctrlKey: false, altKey: false, buttons: 0, deltaMode: 0 }, true, "pan"],
    [{ ctrlKey: false, altKey: false, buttons: 0, deltaMode: 1 }, true, "native"],
    [{ ctrlKey: false, altKey: true, buttons: 0, deltaMode: 0 }, true, "native"],
    [{ ctrlKey: true, altKey: false, buttons: 0, deltaMode: 0 }, true, "zoom"],
    [{ ctrlKey: true, altKey: false, buttons: 0, deltaMode: 0 }, false, "zoom"],
    [{ ctrlKey: false, altKey: false, buttons: 0, deltaMode: 0 }, false, "native"],
    [{ ctrlKey: false, altKey: false, buttons: 1, deltaMode: 0 }, true, "native"],
    [{ ctrlKey: true, altKey: false, buttons: 1, deltaMode: 0 }, false, "native"],
  ] as const)("%j mac=%s is %s", (event, mac, action) => {
    expect(classifyWheel(event, mac)).toBe(action);
  });
});

describe("pan", () => {
  // 2 tan(11°) · 100 units fit CSS_HEIGHT pixels.
  const perPixel = 2 * Math.tan((11 * Math.PI) / 180) * 100 / CSS_HEIGHT;

  it.each([
    ["right", 40, 0],
    ["left", -40, 0],
    ["up", 0, 40],
    ["down", 0, -40],
    ["diagonally", 30, -20],
  ])("moves %s, shifting position and both targets while keeping zoom", (_, deltaX, deltaY) => {
    const { viewer, camera, canvas, wrapper, controlsTarget } = makeViewer();
    installNavigation(viewer);
    const event = wheel(canvas, { deltaX, deltaY });
    expect(event.defaultPrevented).toBe(true);
    runFrame();
    const shift = { x: deltaX * perPixel, y: -deltaY * perPixel };
    expect(camera.position.x).toBeCloseTo(1 + shift.x, 9);
    expect(camera.position.y).toBeCloseTo(2 + shift.y, 9);
    expect(camera.position.z).toBeCloseTo(103, 9);
    for (const target of [controlsTarget, wrapper.target]) {
      expect(target.x).toBeCloseTo(1 + shift.x, 9);
      expect(target.y).toBeCloseTo(2 + shift.y, 9);
      expect(target.z).toBeCloseTo(3, 9);
    }
    expect(camera.zoom).toBe(1);
  });
  it("shifts a target shared by both owners once", () => {
    const { viewer, canvas, controlsTarget } = makeViewer({ sharedTarget: true });
    installNavigation(viewer);
    wheel(canvas, { deltaX: 100 });
    runFrame();
    expect(controlsTarget.x).toBeCloseTo(1 + 100 * perPixel, 9);
  });
  it("uses the orthographic frustum height and zoom", () => {
    const { viewer, camera, canvas } = makeViewer({ ortho: true });
    camera.zoom = 2;
    installNavigation(viewer);
    wheel(canvas, { deltaX: 8 });
    runFrame();
    expect(camera.position.x).toBeCloseTo(1 + 8 * 50 / CSS_HEIGHT, 9);
    expect(camera.zoom).toBe(2);
  });
  it("batches a frame's events into one update", () => {
    const { viewer, camera, canvas, controls } = makeViewer();
    installNavigation(viewer);
    for (let i = 0; i < 5; i++) wheel(canvas, { deltaX: 10 });
    expect(camera.position.x).toBe(1);
    expect(runFrame()).toBe(1);
    expect(controls.update).toHaveBeenCalledTimes(1);
    expect(viewer.update).toHaveBeenCalledTimes(1);
    expect(camera.position.x).toBeCloseTo(1 + 50 * perPixel, 9);
  });
  it("does not paint twice when the controls' change event paints", () => {
    const { viewer, canvas, controls, native } = makeViewer();
    let listener = () => {};
    native.addEventListener.mockImplementation((_: string, l: () => void) => { listener = l; });
    controls.update.mockImplementation(() => listener());
    installNavigation(viewer);
    wheel(canvas, { deltaX: 10 });
    runFrame();
    expect(viewer.update).not.toHaveBeenCalled();
    expect(native.removeEventListener).toHaveBeenCalledTimes(1);
  });
});

describe("zoom", () => {
  it("pinches exponentially about the target without panning", () => {
    const { viewer, camera, canvas, controlsTarget, wrapper } = makeViewer();
    installNavigation(viewer);
    const event = wheel(canvas, { ctrlKey: true, deltaY: -20, deltaX: 15 });
    expect(event.defaultPrevented).toBe(true);
    runFrame();
    expect(camera.position.x).toBe(1);
    expect(camera.position.y).toBe(2);
    expect(camera.position.z).toBeCloseTo(3 + 100 * Math.exp(-0.2), 9);
    expect(controlsTarget).toEqual({ x: 1, y: 2, z: 3 });
    expect(wrapper.target).toEqual({ x: 1, y: 2, z: 3 });
  });
  it("zooms an orthographic camera through its zoom", () => {
    const { viewer, camera, canvas } = makeViewer({ ortho: true });
    installNavigation(viewer);
    wheel(canvas, { ctrlKey: true, deltaY: -20 });
    runFrame();
    expect(camera.zoom).toBeCloseTo(Math.exp(0.2), 9);
    expect(camera.position.z).toBe(103);
    expect(camera.updateProjectionMatrix).toHaveBeenCalled();
    expect(viewer.update).toHaveBeenCalledWith(true, true);
  });
  it("limits one event's step and the overall zoom", () => {
    const { viewer, camera, canvas } = makeViewer({ ortho: true });
    installNavigation(viewer);
    wheel(canvas, { ctrlKey: true, deltaY: -1000 });
    runFrame();
    expect(camera.zoom).toBeCloseTo(Math.exp(0.25), 9);
    for (let i = 0; i < 200; i++) wheel(canvas, { ctrlKey: true, deltaY: -25 });
    runFrame();
    expect(camera.zoom).toBe(100);
  });
  it("does not snap a view that native zoom took past the limits", () => {
    const { viewer, camera, canvas } = makeViewer({ ortho: true });
    camera.zoom = 400;
    installNavigation(viewer);
    wheel(canvas, { ctrlKey: true, deltaY: 1 });
    runFrame();
    expect(camera.zoom).toBeCloseTo(400 * Math.exp(-0.01), 6);
  });
  it("applies a Safari gesture once and ignores its ctrl-wheel", () => {
    const { viewer, camera, canvas } = makeViewer({ ortho: true });
    installNavigation(viewer);
    const gesture = (type: string, scale?: number) => {
      const event = Object.assign(new Event(type, { cancelable: true }), { scale });
      canvas.dispatchEvent(event);
      return event;
    };
    expect(gesture("gesturestart", 1).defaultPrevented).toBe(true);
    gesture("gesturechange", 1.5);
    wheel(canvas, { ctrlKey: true, deltaY: -20 });
    runFrame();
    expect(camera.zoom).toBeCloseTo(1.5, 9);
    gesture("gesturechange", 3);
    runFrame();
    expect(camera.zoom).toBeCloseTo(3, 9);
    gesture("gestureend", 3);
    wheel(canvas, { ctrlKey: true, deltaY: -20 });
    runFrame();
    expect(camera.zoom).toBeCloseTo(3 * Math.exp(0.2), 9);
  });
  it("leaves gestures to the engine's touch handling off macOS", () => {
    setPlatform("MacIntel", 5);
    const { viewer, camera, canvas } = makeViewer({ ortho: true });
    installNavigation(viewer);
    const event = Object.assign(new Event("gesturechange", { cancelable: true }), { scale: 2 });
    canvas.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(frames).toHaveLength(0);
    expect(camera.zoom).toBe(1);
  });
});

describe("native input", () => {
  const nativeCase = (init: Partial<WheelEvent>) => {
    const { viewer, camera, canvas, nativeWheel } = makeViewer();
    installNavigation(viewer);
    canvas.addEventListener("wheel", nativeWheel);
    const event = wheel(canvas, { deltaY: 30, ...init });
    return { event, camera, nativeWheel };
  };
  const expectNative = ({ event, nativeWheel }: ReturnType<typeof nativeCase>) => {
    expect(event.defaultPrevented).toBe(false);
    expect(nativeWheel).toHaveBeenCalledTimes(1);
    expect(frames).toHaveLength(0);
  };

  it("keeps ordinary wheel zoom off macOS", () => { setPlatform("Win32"); expectNative(nativeCase({})); });
  it("leaves Option-scroll to native zoom on macOS", () => expectNative(nativeCase({ altKey: true })));
  it("leaves a line-mode wheel to native zoom on macOS", () => expectNative(nativeCase({ deltaMode: 1 })));
  it("leaves the wheel to the engine during a drag", () => expectNative(nativeCase({ buttons: 1 })));
  it("stops the engine from also handling a consumed event", () => {
    const { event, nativeWheel } = nativeCase({ ctrlKey: true });
    expect(event.defaultPrevented).toBe(true);
    expect(nativeWheel).not.toHaveBeenCalled();
  });
  it("respects disabled controls", () => {
    const { viewer, canvas, native } = makeViewer();
    native.enabled = false;
    installNavigation(viewer);
    expect(wheel(canvas, { deltaX: 10 }).defaultPrevented).toBe(false);
    const gesture = Object.assign(new Event("gesturestart", { cancelable: true }), { scale: 1 });
    canvas.dispatchEvent(gesture);
    expect(gesture.defaultPrevented).toBe(false);
    expect(frames).toHaveLength(0);
  });
});

describe("disposal", () => {
  it("removes listeners and drops a pending frame", () => {
    const { viewer, camera, canvas } = makeViewer();
    const dispose = installNavigation(viewer);
    wheel(canvas, { deltaX: 10 });
    dispose();
    dispose();
    runFrame();
    expect(camera.position.x).toBe(1);
    expect(wheel(canvas, { deltaX: 10 }).defaultPrevented).toBe(false);
    expect(frames).toHaveLength(0);
  });
  it("tolerates a viewer disposed before a queued frame", () => {
    const { viewer, canvas } = makeViewer();
    installNavigation(viewer);
    wheel(canvas, { deltaX: 10 });
    (viewer as NavigationViewer).rendered = null;
    expect(() => runFrame()).not.toThrow();
  });
});
