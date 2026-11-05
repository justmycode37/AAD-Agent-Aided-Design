// Chat-only whole-part hover and click selection on the CAD engine's own GPU picker and shader flags.
// The engine's always-on hover tints one face from inside update(), so this replaces that hover on this
// viewer instance only. Install it for simple controls; the local CAD window keeps native face picking.

const DRAG_TRAVEL_PX = 4;
/** three.js FrontSide and DoubleSide. `three` is only a transitive dependency of the pinned engine. */
const FRONT_SIDE = 0;
const DOUBLE_SIDE = 2;

interface ComponentInfo { id: number; path: string; topo: string; solidPath: string | null }

interface Picker {
  pickAt(x: number, y: number, options?: { windowSize?: number }): { info: ComponentInfo } | null;
  setDirty(): void;
  faceMaterial: { side: number; needsUpdate: boolean } | null;
}

interface Highlight {
  setSelected(id: number, flag: boolean): void;
  patchFaceMaterial(material: unknown): void;
  patchEdgeMaterial(material: unknown): void;
  /** Private in the pinned engine's types. The public hover calls flag only a part's faces. */
  _applyHover(key: string, ids: number[]): void;
}

interface NestedGroup {
  highlight: Highlight | null;
  registry: { maxId: number; entries(): Iterable<ComponentInfo> };
  groups: Record<string, { traverse(visit: (object: { material?: unknown }) => void): void } | undefined>;
  backVisible: boolean;
}

/** The parts of the pinned engine (three-cad-viewer 5.1.3) this uses. */
export interface PartSelectionViewer {
  readonly ready: boolean;
  readonly hasAnimationLoop: boolean;
  readonly idPicker: Picker | null;
  readonly shapes: { format?: string } | null;
  getStates?(): Record<string, number[]>;
  update(updateMarker: boolean, notify: boolean): void;
  renderer: { domElement: HTMLElement };
  rendered: { nestedGroup: NestedGroup; controls: { isInteracting(): boolean } };
  pickingController: {
    clearHover(): void;
    pickVisible(info: ComponentInfo): boolean;
    handleHover?: () => void;
    hoverPreselectActive?: () => boolean;
  };
}

export interface PartSelectionCallbacks {
  /** The selected leaf paths, as in `viewer.getStates()` and scene part paths, after every change. */
  onSelection(paths: string[]): void;
  /** A double-click on one part. Only reports the path; sending anything is up to the caller. */
  onReference(path: string): void;
}

export interface PartSelection {
  /** Drops selection and hover. Call on a presentation change. */
  clear(): void;
  /** Re-checks selected parts against the current scene and visibility, and marks picking stale. */
  refresh(): void;
  paths(): string[];
  dispose(): void;
}

/** Display name of a resolved leaf path. */
export function partName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** Same result as the engine's unexported leafPath(): the tree leaf that owns a picked component. */
function leafPath(info: ComponentInfo): string {
  return info.solidPath ?? info.path.replace(/\/(faces|edges|vertices)\/[^/]+$/, "");
}

interface Part { owner: ComponentInfo; ids: number[] }

/**
 * Faces and edges of each leaf. Corner points are left out, because flagging a solid's pick-only corners
 * would draw selection dots on them; a vertex-only leaf has nothing else to show.
 */
function indexParts(entries: Iterable<ComponentInfo>): Map<string, Part> {
  const parts = new Map<string, Part & { vertices: number[] }>();
  for (const info of entries) {
    const leaf = leafPath(info);
    let part = parts.get(leaf);
    if (!part) parts.set(leaf, part = { owner: info, ids: [], vertices: [] });
    (info.topo === "vertex" ? part.vertices : part.ids).push(info.id);
  }
  for (const part of parts.values()) if (!part.ids.length) part.ids = part.vertices;
  return parts;
}

/**
 * Material.clone() keeps userData but not onBeforeCompile, so an analysis view's painted copies claim to
 * be highlight-patched while their shaders ignore the flags. Patching keeps their analysis color.
 */
function repairHighlightPatches(group: NestedGroup["groups"][string], highlight: Highlight): void {
  group?.traverse(object => {
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) {
      if (!material?.userData?.highlightPatched || Object.hasOwn(material, "onBeforeCompile")) continue;
      material.userData.highlightPatched = false;
      if (material.isLineMaterial) highlight.patchEdgeMaterial(material);
      else highlight.patchFaceMaterial(material);
    }
  });
}

export function installPartSelection(viewer: PartSelectionViewer, callbacks: PartSelectionCallbacks): PartSelection {
  const canvas = viewer.renderer.domElement;
  const doc = canvas.ownerDocument;
  const win = doc.defaultView!;
  const native = viewer.pickingController;

  let selected: string[] = [];
  let hovered: string | null = null;
  let highlightInUse: Highlight | null = viewer.ready ? viewer.rendered.nestedGroup.highlight : null;
  let index: { registry: unknown; maxId: number; parts: Map<string, Part> } | null = null;
  let cursor: { x: number; y: number } | null = null;
  /** The press on the canvas in progress, across every pointer still down. */
  let gesture: { pointerId: number; x: number; y: number; travel: number; valid: boolean; shift: boolean } | null = null;
  const pointers = new Set<number>();
  /** The plain single-part clicks a browser dblclick can be made of. */
  let clicks: string[] = [];
  let engaged = false;
  let frame = 0;
  let ownRender = false;
  let stale = true;
  let disposed = false;

  const nested = () => viewer.rendered.nestedGroup;

  const parts = () => {
    const { registry } = nested();
    if (index?.registry !== registry || index.maxId !== registry.maxId) {
      index = { registry, maxId: registry.maxId, parts: indexParts(registry.entries()) };
    }
    return index.parts;
  };

  const render = () => {
    if (!viewer.ready || viewer.hasAnimationLoop) return;
    ownRender = true;
    try { viewer.update(true, false); } finally { ownRender = false; }
  };

  const flagSelected = (path: string, flag: boolean) => {
    const highlight = nested().highlight;
    const part = parts().get(path);
    if (!highlight || !part) return;
    if (flag) repairHighlightPatches(nested().groups[path], highlight);
    for (const id of part.ids) highlight.setSelected(id, flag);
  };

  const setHover = (path: string | null): boolean => {
    if (path === hovered) return false;
    hovered = path;
    const highlight = viewer.ready ? nested().highlight : null;
    if (highlight) {
      if (path) repairHighlightPatches(nested().groups[path], highlight);
      highlight._applyHover(path ? `part:${path}` : "", path ? parts().get(path)?.ids ?? [] : []);
    }
    return true;
  };

  /** Applies a new selection without drawing. The caller renders. */
  const setSelection = (next: string[]): boolean => {
    if (next.length === selected.length && next.every((path, i) => path === selected[i])) return false;
    const previous = selected;
    selected = next;
    if (viewer.ready) {
      for (const path of previous) if (!next.includes(path)) flagSelected(path, false);
      for (const path of next) if (!previous.includes(path)) flagSelected(path, true);
    }
    return true;
  };

  const notify = () => callbacks.onSelection([...selected]);

  /** The engine culls back faces when picking, but section views draw them. A cut face must pick its own part. */
  const syncFaceSide = (side: number): boolean => {
    const picker = viewer.idPicker;
    const material = picker?.faceMaterial;
    if (!picker || !material || material.side === side) return false;
    material.side = side;
    material.needsUpdate = true;
    picker.setDirty();
    return true;
  };

  const pick = (clientX: number, clientY: number): string | null => {
    const picker = viewer.idPicker;
    if (!picker || !viewer.ready) return null;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left, y = clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return null;
    // The exact pixel first: the engine's default window prefers a neighbor's fat edge or corner.
    const read = (windowSize?: number) => {
      const hit = picker.pickAt(x, y, windowSize ? { windowSize } : {});
      return hit && native.pickVisible(hit.info) ? leafPath(hit.info) : null;
    };
    const side = nested().backVisible ? DOUBLE_SIDE : FRONT_SIDE;
    syncFaceSide(side);
    const exact = read(1);
    // The engine creates its pick materials during the first pick.
    return (syncFaceSide(side) ? read(1) : exact) ?? read();
  };

  /** Follows a rebuilt scene and hidden parts. Returns whether the selection changed. */
  const reconcile = (): boolean => {
    if (!viewer.ready) return false;
    const highlight = nested().highlight;
    if (highlight !== highlightInUse) {
      highlightInUse = highlight;
      hovered = null;
      clicks = [];
      return setSelection([]);
    }
    const states = viewer.getStates?.();
    return setSelection(selected.filter(path => {
      const part = parts().get(path);
      // A whole part is still visible when only its edges are drawn.
      return part !== undefined && (states?.[path] ? states[path].includes(1) : native.pickVisible(part.owner));
    }));
  };

  const tick = () => {
    frame = 0;
    if (disposed || !viewer.ready) return;
    const dropped = reconcile();
    if (stale) { viewer.idPicker?.setDirty(); stale = false; }
    const canHover = cursor && !pointers.size && viewer.shapes?.format !== "GDS" && !viewer.rendered.controls.isInteracting();
    const hoverChanged = setHover(canHover ? pick(cursor!.x, cursor!.y) : null);
    if (dropped || hoverChanged) render();
    if (dropped) notify();
  };
  const schedule = () => { frame ||= requestAnimationFrame(tick); };

  const cancelGesture = () => { if (gesture) gesture.valid = false; clicks = []; };
  const releaseAll = () => { cancelGesture(); gesture = null; pointers.clear(); schedule(); };

  const onPointerDown = (e: PointerEvent) => {
    if (pointers.has(e.pointerId)) releaseAll(); // Its release happened where this page could not see it.
    pointers.add(e.pointerId);
    if (gesture) { gesture.valid = false; clicks = []; } // Another finger or button: a pinch or chord.
    else {
      const plainButton = e.button === 0 && !e.altKey && !e.ctrlKey && !e.metaKey;
      gesture = { pointerId: e.pointerId, x: e.clientX, y: e.clientY, travel: 0, valid: plainButton, shift: e.shiftKey };
      if (!plainButton) clicks = [];
    }
  };

  const addTravel = (e: PointerEvent, g: NonNullable<typeof gesture>) => {
    g.travel += Math.hypot(e.clientX - g.x, e.clientY - g.y);
    g.x = e.clientX; g.y = e.clientY;
    if (g.travel > DRAG_TRAVEL_PX) g.valid = false;
  };

  const onWindowPointerMove = (e: PointerEvent) => {
    if (!gesture || !pointers.has(e.pointerId)) return;
    if (e.pointerType === "mouse" && !e.buttons) { releaseAll(); return; }
    if (e.pointerId !== gesture.pointerId) return;
    if (e.buttons !== 1) gesture.valid = false;
    addTravel(e, gesture);
    if (!gesture.valid) { clicks = []; if (setHover(null)) render(); }
  };

  const onWindowPointerUp = (e: PointerEvent) => {
    if (!gesture || !pointers.delete(e.pointerId)) return;
    if (e.pointerId === gesture.pointerId) {
      addTravel(e, gesture);
      const press = gesture;
      press.pointerId = -1;
      if (press.valid && e.button === 0 && !pointers.size) click(e, press.shift);
      else clicks = [];
    }
    if (!pointers.size) gesture = null;
    schedule();
  };

  const click = (e: PointerEvent, shift: boolean) => {
    viewer.idPicker?.setDirty(); // Explode, visibility and clip changes do not all invalidate the engine's buffer.
    stale = false;
    const path = pick(e.clientX, e.clientY);
    let changed: boolean;
    if (shift) {
      clicks = [];
      changed = !!path && setSelection(selected.includes(path) ? selected.filter(p => p !== path) : [...selected, path]);
    } else if (path) {
      clicks = [...clicks, path].slice(-2);
      changed = setSelection([path]);
    } else {
      clicks = [];
      changed = setSelection([]);
    }
    if (changed) { render(); notify(); }
  };

  const onPointerCancel = (e: PointerEvent) => {
    if (!pointers.delete(e.pointerId)) return;
    cancelGesture();
    if (!pointers.size) gesture = null;
    schedule();
  };

  const onDoubleClick = (e: MouseEvent) => {
    const [first, second] = clicks;
    clicks = [];
    if (first === undefined || first !== second || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    if (setSelection([first])) { render(); notify(); }
    callbacks.onReference(first);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || !engaged || e.defaultPrevented) return;
    // Focus on a text field or control means Escape belongs to that element.
    if (e.target !== canvas && e.target !== doc.body && e.target !== doc.documentElement) return;
    cancelGesture();
    if (setSelection([])) { e.preventDefault(); render(); notify(); }
  };

  const onCanvasMove = (e: PointerEvent) => {
    if (e.pointerType === "touch") return;
    cursor = { x: e.clientX, y: e.clientY };
    schedule();
  };
  const onCanvasLeave = () => {
    cursor = null;
    if (setHover(null)) render();
  };
  const onWindowPointerDown = (e: PointerEvent) => { engaged = e.target === canvas; };
  const onWheel = () => { if (gesture) cancelGesture(); };

  // Runs inside the engine's update(), where native hover used to. Disabling hoverPreselectActive also stops
  // the native move listener from drawing a full frame for every pointer move.
  native.clearHover();
  native.hoverPreselectActive = () => false;
  native.handleHover = () => {
    if (ownRender) return;
    stale = true;
    schedule();
  };

  const capture = { capture: true };
  canvas.addEventListener("pointerdown", onPointerDown as EventListener, capture);
  canvas.addEventListener("pointermove", onCanvasMove as EventListener);
  canvas.addEventListener("pointerleave", onCanvasLeave);
  canvas.addEventListener("dblclick", onDoubleClick as EventListener);
  canvas.addEventListener("contextmenu", cancelGesture, capture);
  win.addEventListener("pointerdown", onWindowPointerDown as EventListener, capture);
  win.addEventListener("pointermove", onWindowPointerMove as EventListener, capture);
  win.addEventListener("pointerup", onWindowPointerUp as EventListener, capture);
  win.addEventListener("pointercancel", onPointerCancel as EventListener, capture);
  win.addEventListener("wheel", onWheel, { capture: true, passive: true });
  win.addEventListener("blur", releaseAll);
  doc.addEventListener("keydown", onKeyDown as EventListener);

  return {
    paths: () => [...selected],
    clear() {
      if (disposed) return;
      cancelGesture();
      stale = true;
      const changed = setSelection([]);
      if (setHover(null) || changed) render();
      if (changed) notify();
      schedule();
    },
    refresh() {
      if (disposed) return;
      stale = true;
      const dropped = reconcile();
      if (dropped) { render(); notify(); }
      schedule();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (frame) cancelAnimationFrame(frame);
      canvas.removeEventListener("pointerdown", onPointerDown as EventListener, capture);
      canvas.removeEventListener("pointermove", onCanvasMove as EventListener);
      canvas.removeEventListener("pointerleave", onCanvasLeave);
      canvas.removeEventListener("dblclick", onDoubleClick as EventListener);
      canvas.removeEventListener("contextmenu", cancelGesture, capture);
      win.removeEventListener("pointerdown", onWindowPointerDown as EventListener, capture);
      win.removeEventListener("pointermove", onWindowPointerMove as EventListener, capture);
      win.removeEventListener("pointerup", onWindowPointerUp as EventListener, capture);
      win.removeEventListener("pointercancel", onPointerCancel as EventListener, capture);
      win.removeEventListener("wheel", onWheel, capture);
      win.removeEventListener("blur", releaseAll);
      doc.removeEventListener("keydown", onKeyDown as EventListener);
      // Removing the instance overrides restores the engine's prototype methods.
      delete native.handleHover;
      delete native.hoverPreselectActive;
      if (viewer.ready) {
        syncFaceSide(FRONT_SIDE);
        setHover(null);
        setSelection([]);
        render();
      }
    },
  };
}
