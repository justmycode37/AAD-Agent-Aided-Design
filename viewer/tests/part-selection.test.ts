import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HOVER_TINT, installPartSelection, partName, SELECTED_TINT, softenHighlightShader, type PartSelectionViewer } from "../src/part-selection";

const SELECTED = 1, HOVER = 2;
const A = "/Group/a", B = "/Group/b";
const entries = [
  { id: 1, path: `${A}/faces/f1`, topo: "face", solidPath: A },
  { id: 2, path: `${A}/faces/f2`, topo: "face", solidPath: A },
  { id: 3, path: `${A}/edges/e1`, topo: "edge", solidPath: A },
  { id: 4, path: `${A}/vertices/v1`, topo: "vertex", solidPath: A },
  { id: 5, path: `${B}/faces/f1`, topo: "face", solidPath: B },
  { id: 6, path: `${B}/edges/e1`, topo: "edge", solidPath: B },
  { id: 7, path: "/Group/wire/edges/e1", topo: "edge", solidPath: null },
];

/** The engine's _install() for a material, with shader text in the engine's exact form. */
function nativePatch(material: Record<string, unknown>) {
  material.userData = { highlightPatched: true };
  material.customProgramCacheKey = () => "hl:patchFaceMaterial::";
  material.onBeforeCompile = (shader: { fragmentShader: string; vertexShader: string }) => {
    shader.fragmentShader += "diffuseColor.rgb = uHighlightSelectedColor;diffuseColor.rgb = uHighlightHoverColor;";
    shader.vertexShader += "uint hlEdgeState = highlightState();\n\t\t\t\toffset *= ((hlEdgeState & 2u) != 0u ? 5.0 : ((hlEdgeState & 1u) != 0u ? 3.0 : linewidth));";
  };
  return material;
}
const compiled = (material: { onBeforeCompile(shader: unknown, renderer: unknown): void }) => {
  const shader = { vertexShader: "", fragmentShader: "" };
  material.onBeforeCompile(shader, null);
  return shader;
};

let frames: FrameRequestCallback[] = [];
const runFrames = () => { while (frames.length) frames.splice(0).forEach(cb => cb(0)); };

function setup() {
  const win = new EventTarget() as EventTarget & Window;
  const doc = Object.assign(new EventTarget(), { defaultView: win, body: new EventTarget(), documentElement: new EventTarget() });
  const canvas = Object.assign(new EventTarget(), {
    ownerDocument: doc,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 300 }),
  }) as unknown as HTMLElement;
  /** x picks by column: 100 and 110 are two faces of A, 200 is B, anything else is background. */
  const pickAt = vi.fn((x: number) => {
    const id = x === 100 ? 1 : x === 110 ? 2 : x === 120 ? 3 : x === 200 ? 5 : x === 300 ? 7 : 0;
    return id ? { info: entries[id - 1], id, point: null } : null;
  });
  const flags = new Map<number, number>();
  const set = (id: number, bit: number, on: boolean) => flags.set(id, on ? (flags.get(id) ?? 0) | bit : (flags.get(id) ?? 0) & ~bit);
  let hoverIds: number[] = [];
  const highlight = {
    setSelected: (id: number, flag: boolean) => set(id, SELECTED, flag),
    patchFaceMaterial: vi.fn(nativePatch),
    patchEdgeMaterial: vi.fn(nativePatch),
    _applyHover: (_key: string, ids: number[]) => { hoverIds.forEach(id => set(id, HOVER, false)); hoverIds = ids; ids.forEach(id => set(id, HOVER, true)); },
  };
  const hidden = new Set<string>();
  class Controller {
    handleHover() { return "native-handle"; }
    hoverPreselectActive() { return true; }
    clearHover = vi.fn();
    pickVisible = (info: { solidPath: string | null }) => !hidden.has(info.solidPath ?? "");
  }
  const pickingController = new Controller();
  const interacting = { value: false };
  const group = { materials: [] as unknown[], traverse(visit: (o: { material?: unknown }) => void) { this.materials.forEach(material => visit({ material })); } };
  const nestedGroup = {
    highlight,
    registry: { maxId: entries.length, entries: () => entries },
    groups: { [A]: group },
    backVisible: false,
  };
  const idPicker = { pickAt, setDirty: vi.fn(), faceMaterial: { side: 0, needsUpdate: false } };
  const viewer = {
    ready: true,
    hasAnimationLoop: false,
    idPicker,
    shapes: {},
    getStates: undefined as undefined | (() => Record<string, number[]>),
    update: vi.fn(() => (viewer.pickingController as unknown as { handleHover(): void }).handleHover()),
    renderer: { domElement: canvas },
    rendered: { nestedGroup, controls: { isInteracting: () => interacting.value } },
    pickingController,
  };
  const onSelection = vi.fn(), onReference = vi.fn(), onHover = vi.fn();
  const selection = installPartSelection(viewer as unknown as PartSelectionViewer, { onSelection, onReference, onHover });
  const pointer = (type: string, x: number, init: Record<string, unknown> = {}) => {
    const event = Object.assign(new Event(type), { pointerId: 1, pointerType: "mouse", clientX: x, clientY: 50, button: 0, buttons: type === "pointerup" ? 0 : 1, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...init });
    if (type === "pointerdown") { const press = new Event(type); Object.defineProperty(press, "target", { value: canvas }); win.dispatchEvent(press); canvas.dispatchEvent(event); }
    else win.dispatchEvent(event);
  };
  const click = (x: number, init: Record<string, unknown> = {}) => { pointer("pointerdown", x, init); pointer("pointerup", x, init); };
  const dblclick = (init: Record<string, unknown> = {}) => canvas.dispatchEvent(Object.assign(new Event("dblclick"), { shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...init }));
  const hover = (x: number) => { canvas.dispatchEvent(Object.assign(new Event("pointermove"), { pointerType: "mouse", clientX: x, clientY: 50 })); runFrames(); };
  const key = (target: EventTarget, name = "Escape") => {
    const event = Object.assign(new Event("keydown", { cancelable: true }), { key: name });
    Object.defineProperty(event, "target", { value: target });
    doc.dispatchEvent(event);
    return event;
  };
  return { viewer, canvas, win, doc, selection, onSelection, onReference, onHover, flags, hidden, pointer, click, dblclick, hover, key, pickAt, idPicker, highlight, nestedGroup, group, interacting, pickingController };
}

const withBit = (flags: Map<number, number>, bit: number) => [...flags].filter(([, value]) => value & bit).map(([id]) => id).sort();

beforeEach(() => {
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", (handle: number) => { frames[handle - 1] = () => {}; });
});
afterEach(() => vi.unstubAllGlobals());

describe("hover", () => {
  it("reports owning parts once and clears on blank, leave and disposal", () => {
    const t = setup();
    t.hover(100); t.hover(110);
    expect(t.onHover.mock.calls).toEqual([[A]]);
    t.hover(200);
    expect(t.onHover).toHaveBeenLastCalledWith(B);
    t.hover(50);
    expect(t.onHover).toHaveBeenLastCalledWith(null);
    t.hover(100);
    t.canvas.dispatchEvent(new Event("pointerleave"));
    expect(t.onHover).toHaveBeenLastCalledWith(null);
    t.hover(100);
    t.selection.dispose();
    expect(t.onHover).toHaveBeenLastCalledWith(null);
  });

  it("flags every face and edge of the owning part, whichever face is under the pointer", () => {
    const t = setup();
    t.hover(100);
    expect(withBit(t.flags, HOVER)).toEqual([1, 2, 3]);
    t.hover(110);
    expect(withBit(t.flags, HOVER)).toEqual([1, 2, 3]);
    t.hover(200);
    expect(withBit(t.flags, HOVER)).toEqual([5, 6]);
    expect(t.onSelection).not.toHaveBeenCalled();
  });

  it("resolves a standalone edge leaf by stripping its topology suffix", () => {
    const t = setup();
    t.hover(300);
    expect(withBit(t.flags, HOVER)).toEqual([7]);
    t.click(300);
    expect(t.selection.paths()).toEqual(["/Group/wire"]);
  });

  it("draws only when the hovered part changes and clears on leaving the canvas", () => {
    const t = setup();
    t.hover(100);
    expect(t.viewer.update).toHaveBeenCalledTimes(1);
    t.hover(110);
    expect(t.viewer.update).toHaveBeenCalledTimes(1);
    t.canvas.dispatchEvent(new Event("pointerleave"));
    expect(withBit(t.flags, HOVER)).toEqual([]);
    expect(t.viewer.update).toHaveBeenCalledTimes(2);
  });

  it("keeps native hover from running on an engine update, and marks picking stale", () => {
    const t = setup();
    expect(t.pickingController.clearHover).toHaveBeenCalledOnce();
    expect(t.pickingController.hoverPreselectActive()).toBe(false);
    t.idPicker.setDirty.mockClear();
    t.viewer.update(); // A camera or visibility change from elsewhere.
    runFrames();
    expect(t.idPicker.setDirty).toHaveBeenCalledOnce();
    t.idPicker.setDirty.mockClear();
    t.hover(100); // Its own highlight render must not invalidate the buffer again.
    t.hover(110);
    expect(t.idPicker.setDirty).not.toHaveBeenCalled();
  });

  it("does not hover during a press, an interaction or touch", () => {
    const t = setup();
    t.pointer("pointerdown", 100);
    t.hover(100);
    expect(withBit(t.flags, HOVER)).toEqual([]);
    t.pointer("pointerup", 100);
    t.interacting.value = true;
    t.hover(100);
    expect(withBit(t.flags, HOVER)).toEqual([]);
    t.interacting.value = false;
    t.canvas.dispatchEvent(Object.assign(new Event("pointermove"), { pointerType: "touch", clientX: 100, clientY: 50 }));
    expect(frames.length).toBe(0);
  });
});

describe("click selection", () => {
  it("selects one whole part, replaces it on another click, and clears on blank", () => {
    const t = setup();
    t.click(100);
    expect(t.onSelection).toHaveBeenLastCalledWith([A]);
    expect(withBit(t.flags, SELECTED)).toEqual([1, 2, 3]);
    t.click(200);
    expect(t.onSelection).toHaveBeenLastCalledWith([B]);
    expect(withBit(t.flags, SELECTED)).toEqual([5, 6]);
    t.click(50);
    expect(t.onSelection).toHaveBeenLastCalledWith([]);
    expect(withBit(t.flags, SELECTED)).toEqual([]);
  });

  it("deselects on a second click, from any face or edge of the part", () => {
    const t = setup();
    t.click(100);
    t.click(110);
    expect(t.selection.paths()).toEqual([]);
    expect(t.onSelection).toHaveBeenLastCalledWith([]);
    expect(withBit(t.flags, SELECTED)).toEqual([]);
    t.click(120); // An edge of the same part.
    expect(t.selection.paths()).toEqual([A]);
    t.click(100);
    expect(t.selection.paths()).toEqual([]);
    expect(t.onSelection).toHaveBeenCalledTimes(4);
  });

  it("a plain click on one of several selected parts deselects only that part, and on another part replaces them", () => {
    const t = setup();
    t.click(100);
    t.click(200, { shiftKey: true });
    t.click(100);
    expect(t.selection.paths()).toEqual([B]);
    t.click(100, { shiftKey: true });
    t.click(300);
    expect(t.selection.paths()).toEqual(["/Group/wire"]);
  });

  it("keeps hover and selection distinct", () => {
    const t = setup();
    t.click(100);
    t.hover(200);
    expect(withBit(t.flags, SELECTED)).toEqual([1, 2, 3]);
    expect(withBit(t.flags, HOVER)).toEqual([5, 6]);
    t.hover(50);
    expect(withBit(t.flags, SELECTED)).toEqual([1, 2, 3]);
    expect(withBit(t.flags, HOVER)).toEqual([]);
  });

  it("re-renders the pick buffer for each click", () => {
    const t = setup();
    t.idPicker.setDirty.mockClear();
    t.click(100);
    expect(t.idPicker.setDirty).toHaveBeenCalled();
  });

  it("Shift toggles parts and leaves the selection alone on background", () => {
    const t = setup();
    t.click(100);
    t.click(200, { shiftKey: true });
    expect(t.selection.paths()).toEqual([A, B]);
    t.click(50, { shiftKey: true });
    expect(t.selection.paths()).toEqual([A, B]);
    t.click(110, { shiftKey: true });
    expect(t.selection.paths()).toEqual([B]);
  });

  it("treats small pointer jitter as a click", () => {
    const t = setup();
    t.pointer("pointerdown", 100);
    t.pointer("pointermove", 103);
    t.pointer("pointerup", 103);
    expect(t.selection.paths()).toEqual([]); // x=103 is background in this stub: blank click, not a drag.
    t.pointer("pointerdown", 200);
    t.pointer("pointermove", 202);
    t.pointer("pointerup", 200);
    expect(t.selection.paths()).toEqual([B]);
  });
});

describe("gestures that are not clicks", () => {
  const start = (t: ReturnType<typeof setup>) => { t.click(200); t.onSelection.mockClear(); };

  it("ignores a drag that returns to its start", () => {
    const t = setup();
    start(t);
    t.pointer("pointerdown", 100);
    t.pointer("pointermove", 130);
    t.pointer("pointermove", 100);
    t.pointer("pointerup", 100);
    expect(t.onSelection).not.toHaveBeenCalled();
    expect(t.selection.paths()).toEqual([B]);
  });

  it("counts total travel, so a wiggle past the threshold is a drag", () => {
    const t = setup();
    start(t);
    t.pointer("pointerdown", 100);
    for (const x of [103, 100, 103, 100]) t.pointer("pointermove", x);
    t.pointer("pointerup", 100);
    expect(t.onSelection).not.toHaveBeenCalled();
  });

  it("counts travel when the final move arrives only with the release", () => {
    const t = setup();
    start(t);
    t.pointer("pointerdown", 180);
    t.pointer("pointerup", 200);
    expect(t.onSelection).not.toHaveBeenCalled();
  });

  it("ignores a pinch, whichever finger lifts first", () => {
    const t = setup();
    start(t);
    t.pointer("pointerdown", 100, { pointerId: 1, pointerType: "touch" });
    t.pointer("pointerdown", 100, { pointerId: 2, pointerType: "touch" });
    t.pointer("pointerup", 100, { pointerId: 2, pointerType: "touch" });
    t.pointer("pointerup", 100, { pointerId: 1, pointerType: "touch" });
    t.pointer("pointerdown", 100, { pointerId: 3, pointerType: "touch" });
    t.pointer("pointerdown", 100, { pointerId: 4, pointerType: "touch" });
    t.pointer("pointerup", 100, { pointerId: 3, pointerType: "touch" });
    t.pointer("pointerup", 100, { pointerId: 4, pointerType: "touch" });
    expect(t.onSelection).not.toHaveBeenCalled();
  });

  it("selects on a single-finger tap", () => {
    const t = setup();
    t.click(100, { pointerType: "touch" });
    expect(t.selection.paths()).toEqual([A]);
  });

  it("ignores cancel, right and modified clicks, wheel, context menu and chorded buttons", () => {
    const t = setup();
    start(t);
    t.pointer("pointerdown", 100);
    t.win.dispatchEvent(Object.assign(new Event("pointercancel"), { pointerId: 1 }));
    t.pointer("pointerup", 100);
    t.click(100, { button: 2 });
    t.click(100, { altKey: true });
    t.click(100, { ctrlKey: true });
    t.click(100, { metaKey: true });
    t.pointer("pointerdown", 100);
    t.win.dispatchEvent(new Event("wheel"));
    t.pointer("pointerup", 100);
    t.pointer("pointerdown", 100);
    t.canvas.dispatchEvent(new Event("contextmenu"));
    t.pointer("pointerup", 100);
    t.pointer("pointerdown", 100);
    t.pointer("pointermove", 100, { buttons: 3 });
    t.pointer("pointerup", 100);
    t.pointer("pointerdown", 100);
    t.win.dispatchEvent(new Event("blur"));
    t.pointer("pointerup", 100);
    expect(t.onSelection).not.toHaveBeenCalled();
  });

  it("recovers from a release it never saw", () => {
    const t = setup();
    start(t);
    t.pointer("pointerdown", 100);
    t.pointer("pointermove", 100, { buttons: 0 });
    t.pointer("pointerup", 100);
    expect(t.onSelection).not.toHaveBeenCalled();
    t.pointer("pointerdown", 100);
    t.pointer("pointerdown", 100);
    t.pointer("pointerup", 100);
    expect(t.selection.paths()).toEqual([A]);
  });

  it("ignores a release from a pointer that did not start on the canvas", () => {
    const t = setup();
    t.pointer("pointerup", 100);
    t.pointer("pointermove", 100);
    expect(t.onSelection).not.toHaveBeenCalled();
  });
});

describe("double-click", () => {
  it("references the part exactly once, without reselecting what its second click cleared", () => {
    const t = setup();
    t.click(100);
    t.click(110);
    t.dblclick();
    expect(t.selection.paths()).toEqual([]);
    expect(withBit(t.flags, SELECTED)).toEqual([]);
    expect(t.onSelection.mock.calls).toEqual([[[A]], [[]]]);
    expect(t.onReference).toHaveBeenCalledTimes(1);
    expect(t.onReference).toHaveBeenCalledWith(A);
    t.dblclick();
    expect(t.onReference).toHaveBeenCalledTimes(1);
  });

  it("leaves a part selected when the double-click started from selected", () => {
    const t = setup();
    t.click(100);
    t.onSelection.mockClear();
    t.click(100);
    t.click(100);
    t.dblclick();
    expect(t.selection.paths()).toEqual([A]);
    expect(t.onReference).toHaveBeenCalledExactlyOnceWith(A);
    expect(t.onSelection).toHaveBeenCalledTimes(2);
  });

  it("ignores background, different parts, a drag, and modifiers", () => {
    const t = setup();
    t.click(50); t.click(50); t.dblclick();
    t.click(100); t.click(200); t.dblclick();
    t.click(100);
    t.pointer("pointerdown", 100); t.pointer("pointermove", 130); t.pointer("pointerup", 100);
    t.click(100);
    t.dblclick();
    t.pointer("pointerdown", 100); t.pointer("pointermove", 130); t.pointer("pointerup", 100);
    t.pointer("pointerdown", 100); t.pointer("pointermove", 130); t.pointer("pointerup", 100);
    t.dblclick();
    t.click(100, { shiftKey: true }); t.click(100, { shiftKey: true }); t.dblclick({ shiftKey: true });
    t.click(100); t.click(100); t.dblclick({ metaKey: true });
    expect(t.onReference).not.toHaveBeenCalled();
  });
});

describe("Escape", () => {
  it("clears after canvas use, but not from a text field, control, or before canvas use", () => {
    const t = setup();
    t.click(100);
    const input = new EventTarget();
    expect(t.key(input).defaultPrevented).toBe(false);
    expect(t.selection.paths()).toEqual([A]);
    t.key(t.doc.body, "a");
    expect(t.selection.paths()).toEqual([A]);
    t.win.dispatchEvent(new Event("pointerdown"));
    t.key(t.doc.body);
    expect(t.selection.paths()).toEqual([A]); // The last press was elsewhere.
    t.click(200);
    expect(t.key(t.doc.body).defaultPrevented).toBe(true);
    expect(t.selection.paths()).toEqual([]);
    expect(t.onSelection).toHaveBeenLastCalledWith([]);
  });
});

describe("lifecycle", () => {
  it("clear() drops selection and hover, and notifies only on a change", () => {
    const t = setup();
    t.click(100); t.hover(100);
    t.selection.clear();
    expect(t.selection.paths()).toEqual([]);
    expect(withBit(t.flags, SELECTED)).toEqual([]);
    expect(t.onSelection).toHaveBeenCalledTimes(2);
    t.selection.clear();
    expect(t.onSelection).toHaveBeenCalledTimes(2);
  });

  it("drops a selected part that becomes hidden, on the frame after an engine update", () => {
    const t = setup();
    t.click(100);
    t.hidden.add(A);
    t.viewer.update();
    runFrames();
    expect(t.onSelection).toHaveBeenLastCalledWith([]);
    expect(withBit(t.flags, SELECTED)).toEqual([]);
  });

  it("keeps a selected part whose faces are hidden while its edges are drawn, using optional getStates", () => {
    const t = setup();
    t.click(100);
    t.hidden.add(A); // The engine's pickVisible() reads faces, so it now reports a false negative.
    t.viewer.getStates = () => ({ [A]: [0, 1], [B]: [1, 1] });
    t.selection.refresh();
    expect(t.selection.paths()).toEqual([A]);
    t.viewer.update();
    runFrames();
    expect(t.selection.paths()).toEqual([A]);
    t.viewer.getStates = () => ({ [A]: [0, 0], [B]: [1, 1] });
    t.selection.refresh();
    expect(t.selection.paths()).toEqual([]);
    expect(t.onSelection).toHaveBeenLastCalledWith([]);
  });

  it("refresh() drops parts missing from a rebuilt scene and follows a new highlight controller", () => {
    const t = setup();
    t.click(100);
    t.hover(100);
    t.nestedGroup.highlight = { ...t.highlight };
    t.selection.refresh();
    expect(t.onSelection).toHaveBeenLastCalledWith([]);
    expect(t.onHover).toHaveBeenLastCalledWith(null);
    t.click(100);
    expect(t.selection.paths()).toEqual([A]);
  });

  it("patches an analysis clone that lost its highlight shader, softens it, and leaves others alone", () => {
    const t = setup();
    const clone = { userData: { highlightPatched: true } };
    const line = { userData: { highlightPatched: true }, isLineMaterial: true };
    const foreign = { userData: { highlightPatched: true }, onBeforeCompile() {} };
    const back = { userData: {} };
    t.group.materials.push(clone, [line], foreign, back);
    t.hover(100);
    expect(t.highlight.patchFaceMaterial).toHaveBeenCalledExactlyOnceWith(clone);
    expect(t.highlight.patchEdgeMaterial).toHaveBeenCalledExactlyOnceWith(line);
    expect(compiled(clone as never).fragmentShader).not.toContain("diffuseColor.rgb = uHighlightSelectedColor;");
    expect(compiled(line as never).vertexShader).toBe("offset *= linewidth;");
    expect(Object.keys(back)).toEqual(["userData"]);
  });

  it("softens this viewer's materials once, and only the engine's highlight shader", () => {
    const t = setup();
    const native = nativePatch({ needsUpdate: false, color: "keep" }) as never as { onBeforeCompile: never; customProgramCacheKey(): string; needsUpdate: boolean; color: string };
    const { onBeforeCompile: compile } = native;
    const triplanar = { userData: { highlightPatched: true }, onBeforeCompile() {}, customProgramCacheKey: () => "triplanar" };
    const points = Object.assign(nativePatch({}), { isPointsMaterial: true });
    const pointsCompile = points.onBeforeCompile;
    t.group.materials.push(native, triplanar, points);
    const elsewhere = nativePatch({ needsUpdate: false }) as { needsUpdate: boolean; onBeforeCompile: unknown };
    const elsewhereCompile = elsewhere.onBeforeCompile;
    t.hover(200);
    expect(native.needsUpdate).toBe(false); // Not touched until its part is.
    t.hover(100);
    expect(native.needsUpdate).toBe(true);
    expect(native.customProgramCacheKey()).toBe("hl:patchFaceMaterial:::aad-soft-highlight");
    const wrapper = native.onBeforeCompile;
    expect(wrapper).not.toBe(compile);
    t.hover(50);
    t.click(100);
    expect(native.onBeforeCompile).toBe(wrapper);
    expect(native.color).toBe("keep");
    expect(triplanar.customProgramCacheKey()).toBe("triplanar");
    expect(points.onBeforeCompile).toBe(pointsCompile);
    expect(elsewhere.onBeforeCompile).toBe(elsewhereCompile);
    expect(elsewhere.needsUpdate).toBe(false);
    expect(Object.hasOwn(Object.getPrototypeOf(native), "onBeforeCompile")).toBe(false);
  });

  it("dispose restores the native shader functions, and a later replacement survives", () => {
    const t = setup();
    const material = nativePatch({ needsUpdate: false }) as { onBeforeCompile: unknown; customProgramCacheKey(): string; needsUpdate: boolean };
    const replaced = nativePatch({}) as { onBeforeCompile: unknown };
    const { onBeforeCompile, customProgramCacheKey } = material;
    t.group.materials.push(material, replaced);
    t.click(100);
    const later = () => {};
    replaced.onBeforeCompile = later;
    material.needsUpdate = false;
    t.selection.dispose();
    expect(material.onBeforeCompile).toBe(onBeforeCompile);
    expect(material.customProgramCacheKey).toBe(customProgramCacheKey);
    expect(material.needsUpdate).toBe(true);
    expect(replaced.onBeforeCompile).toBe(later);
    expect(compiled(material as never).fragmentShader).toContain("diffuseColor.rgb = uHighlightSelectedColor;");
  });

  it("restores and forgets a painted copy when the analysis view disposes it", () => {
    const t = setup();
    const listeners: (() => void)[] = [];
    const copy = { userData: { highlightPatched: true }, addEventListener: (_: string, listener: () => void) => listeners.push(listener) } as { userData: object; onBeforeCompile?: unknown; addEventListener: unknown };
    t.group.materials.push(copy);
    t.hover(100);
    const soft = copy.onBeforeCompile;
    listeners[0]();
    expect(copy.onBeforeCompile).not.toBe(soft);
    t.hover(50);
    t.group.materials.length = 0;
    t.selection.dispose();
    expect(listeners).toHaveLength(1);
  });

  it("uses double-sided face picking while back faces are drawn, as in a section", () => {
    const t = setup();
    t.nestedGroup.backVisible = true;
    t.click(100);
    expect(t.idPicker.faceMaterial.side).toBe(2);
    t.nestedGroup.backVisible = false;
    t.click(100);
    expect(t.idPicker.faceMaterial.side).toBe(0);
  });

  it("dispose removes hover and selection, restores native hover, and stops listening", () => {
    const t = setup();
    t.click(100); t.hover(100);
    t.nestedGroup.backVisible = true; t.click(100);
    t.onSelection.mockClear();
    t.canvas.dispatchEvent(Object.assign(new Event("pointermove"), { pointerType: "mouse", clientX: 200, clientY: 50 }));
    t.selection.dispose();
    runFrames();
    expect(withBit(t.flags, SELECTED)).toEqual([]);
    expect(withBit(t.flags, HOVER)).toEqual([]);
    expect(t.idPicker.faceMaterial.side).toBe(0);
    expect(Object.hasOwn(t.pickingController, "handleHover")).toBe(false);
    expect(t.pickingController.handleHover()).toBe("native-handle");
    expect(t.pickingController.hoverPreselectActive()).toBe(true);
    t.viewer.update.mockClear();
    t.click(200); t.hover(200); t.dblclick();
    t.key(t.doc.body);
    expect(t.onSelection).not.toHaveBeenCalled();
    expect(t.onReference).not.toHaveBeenCalled();
    t.selection.dispose();
    expect(t.viewer.update).not.toHaveBeenCalled();
  });

  it("does not touch the scene when disposed after the viewer", () => {
    const t = setup();
    t.viewer.ready = false;
    expect(() => t.selection.dispose()).not.toThrow();
  });
});

it("names a part from its path", () => {
  expect(partName("/Group/shaft assembly/gear")).toBe("gear");
});

describe("soft highlight shader", () => {
  it("keeps selection stronger than hover while retaining the base color and edge width", () => {
    expect(HOVER_TINT).toBeGreaterThan(0);
    expect(SELECTED_TINT).toBeGreaterThan(HOVER_TINT);
    expect(SELECTED_TINT).toBeLessThan(0.5);
    const shader = { vertexShader: "", fragmentShader: "" };
    shader.fragmentShader = "diffuseColor.rgb = uHighlightSelectedColor; else diffuseColor.rgb = uHighlightHoverColor;";
    shader.vertexShader = "uint hlEdgeState = highlightState();\n  offset *= (x ? 5.0 : (y ? 3.0 : linewidth));\nrest";
    softenHighlightShader(shader);
    const blend = (strength: number) => `pow(mix(pow(diffuseColor.rgb, vec3(1.0 / 2.2)), pow(mix(uHighlightSelectedColor, vec3(1.0), 0.35), vec3(1.0 / 2.2)), ${strength.toFixed(2)}), vec3(2.2))`;
    expect(shader.fragmentShader).toContain(blend(SELECTED_TINT));
    expect(shader.fragmentShader).toContain(blend(HOVER_TINT));
    expect(shader.vertexShader).toBe("offset *= linewidth;\nrest");
  });

  it("matches the pinned engine's exact shader text", () => {
    const engine = readFileSync("node_modules/three-cad-viewer/dist/three-cad-viewer.esm.js", "utf8");
    for (const source of [
      "diffuseColor.rgb = ${U_HIGHLIGHT_SELECTED_COLOR};",
      "diffuseColor.rgb = ${U_HIGHLIGHT_HOVER_COLOR};",
      "`uint hlEdgeState = highlightState();",
      "const cacheKey = `hl:${where}:${variant}:${prevKey}`;",
      'const U_HIGHLIGHT_SELECTED_COLOR = "uHighlightSelectedColor";',
      'const U_HIGHLIGHT_HOVER_COLOR = "uHighlightHoverColor";',
    ]) expect(engine).toContain(source);
  });
});

it("the pinned engine still has the internals this relies on", () => {
  const engine = readFileSync("node_modules/three-cad-viewer/dist/three-cad-viewer.esm.js", "utf8");
  for (const source of [
    "_applyHover(key, ids) {",
    "hoverPreselectActive() {",
    "pickVisible(info) {",
    "this.pickingController.handleHover();",
    "this.faceMaterial = createFacePickMaterial(opts);",
    'info.solidPath ?? info.path.replace(/\\/(faces|edges|vertices)\\/[^/]+$/, "")',
  ]) expect(engine).toContain(source);
  expect(engine).toMatch(/hoverPreselectActive\(\) &&\s+this\.host\.ready/);
  const material = readFileSync("node_modules/three-cad-viewer/node_modules/three/src/materials/Material.js", "utf8");
  expect(material.slice(material.indexOf("copy( source ) {"))).not.toContain("onBeforeCompile");
});
