/** Screen-space density for the pinned CAD engine's grid; model geometry is untouched. */
// The native texture uses an 80px font in a 128px canvas: 18px sprites yield ~11px text.
const LABEL_HEIGHT = 18;
const TARGET_SPACING = 85;
export interface LabelBox { x: number; y: number; width: number; height: number }

/** Round upward to a readable 1/2/5 interval. */
export function niceGridStep(value: number): number {
  if (!(value > 0) || !Number.isFinite(value)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const factor = value / power;
  return (factor <= 1 ? 1 : factor <= 2 ? 2 : factor <= 5 ? 5 : 10) * power;
}

export function gridStep(pixelsPerUnit: number, viewportPixels = 1000): number {
  // Bound work by viewport size, so close zoom still reaches fractional intervals.
  return niceGridStep(Math.max(TARGET_SPACING, viewportPixels / 32) / Math.max(pixelsPerUnit, 1e-12));
}

export function gridCoordinate(local: number, offset: number): string {
  return String(Number((local + offset).toPrecision(12)));
}

export function overlaps(a: LabelBox, b: LabelBox, padding = 8): boolean {
  return Math.abs(a.x - b.x) < (a.width + b.width) / 2 + padding &&
    Math.abs(a.y - b.y) < (a.height + b.height) / 2 + padding;
}

/**
 * Adapt the engine instance, preserving native grid toggles, center, theme and CAD tools.
 * Grid.create is synchronous internally despite its Promise signature in three-cad-viewer 5.1.3.
 */
export function installAdaptiveGrid(viewer: any): () => void {
  const grid = viewer.rendered.gridHelper;
  const nativeUpdate = grid.update;
  const nativeCreateLabel = grid.createLabel;
  const halfExtent = grid.size / 2;
  let previousKey = "";
  let patchCenter = [0, 0, 0];
  grid.createLabel = function(_tick: string, x: number, plane: number, horizontal: boolean) {
    const axis = [[0, 1], [2, 0], [1, 2]][plane][horizontal ? 0 : 1];
    const text = gridCoordinate(x, patchCenter[axis]);
    const value = Number(text);
    // Native tick strings have already lost small fractions; use numeric coordinates.
    const label = nativeCreateLabel.call(this, text, x, plane, horizontal);
    label.userData.aadTick = value;
    return label;
  };
  grid.update = function(_zoom: number, force = false, theme: string | null = null) {
    if (theme && theme !== grid.theme) { grid.theme = theme; previousKey = ""; }
    if (!grid.getVisible()) return Promise.resolve();
    const camera = grid.getCamera();
    if (!camera || !(halfExtent > 0)) return Promise.resolve();
    camera.updateMatrixWorld();
    const width = grid.cadWidth, height = grid.height;
    const center = grid.position.clone().set(...viewer.getCameraTarget());
    const origin = center.clone().project(camera);
    const distance = center.distanceTo(camera.position);
    const sample = Math.max(distance / 10000, halfExtent * 1e-10);
    const lengths = ['x', 'y', 'z'].map(axis => {
      const point = center.clone(); point[axis] += sample; point.project(camera);
      return Math.hypot((point.x - origin.x) * width / 2, (point.y - origin.y) * height / 2) / sample;
    }).filter(n => Number.isFinite(n) && n > 1e-8).sort((a, b) => a - b);
    const pixelsPerUnit = lengths[Math.floor(lengths.length / 2)] ?? 0;
    const viewport = Math.max(width, height);
    const step = gridStep(pixelsPerUnit, viewport);
    patchCenter = ['x', 'y', 'z'].map(axis => Math.round(center[axis] / step) * step);
    const halfCells = Math.max(1, Math.floor(Math.min(width, height) * .45 / (Math.max(pixelsPerUnit, 1e-12) * step)));
    const key = [step, halfCells, ...patchCenter].join(':');
    if (key !== previousKey) {
      // Dispose grid lines and cached label resources before rebuilding; no scene meshes are involved.
      for (const group of grid.children) group.children[0]?.traverse((object: any) => {
        object.geometry?.dispose();
        for (const material of [object.material ?? []].flat()) material.dispose();
      });
      grid.clearCache(); grid.clear();
      // Keep the zero-aligned labels at round values, including when the extent changes.
      grid.size = 2 * halfCells * step;
      grid.ticks = grid.size / (2 * step);
      void grid.create(false);
      previousKey = key;
    }
    if (force) grid.setVisible();
    // A bounded patch follows the target; labels retain actual world coordinates.
    for (const group of grid.children) group.position.set(...patchCenter);
    if (!grid.centerGrid) {
      grid.children[0].position.z -= grid.size / 2;
      grid.children[1].position.y -= (grid.flipY ? -1 : 1) * grid.size / 2;
      grid.children[2].position.x -= grid.size / 2;
    }
    grid.updateWorldMatrix(true, true);
    const labelHeight = LABEL_HEIGHT * grid.gridFontSize / 10;
    const labels: { label: any; box: LabelBox; value: number }[] = [];
    for (const group of grid.children) {
      for (const label of group.children.slice(1)) {
        label.visible = false;
        if (!group.visible || !label.isSprite) continue;
        // Keep all coordinates horizontal and at a constant, modest CSS pixel size.
        label.material.rotation = 0;
        const aspect = label.userData.aspectRatio || 4;
        const scale = 2 * labelHeight / (height * Math.abs(camera.projectionMatrix.elements[5]));
        label.scale.set(scale * aspect, scale, 1);
        const point = label.getWorldPosition(label.position.clone()).project(camera);
        const box = { x: (point.x + 1) * width / 2, y: (1 - point.y) * height / 2, width: labelHeight * aspect, height: labelHeight };
        if (![point.x, point.y, point.z].every(Number.isFinite) || point.z < -1 || point.z > 1) continue;
        if (box.x - box.width / 2 < 8 || box.x + box.width / 2 > width - 8 || box.y < 72 || box.y > height - 16) continue;
        if (box.x < 100 && box.y > height - 110) continue; // XYZ orientation marker.
        labels.push({ label, box, value: Math.abs(label.userData.aadTick ?? 0) });
      }
    }
    // Important small coordinates win; suppress duplicate/overlapping labels across all three planes.
    labels.sort((a, b) => a.value - b.value);
    const placed: LabelBox[] = [];
    for (const { label, box } of labels) {
      if (placed.some(other => overlaps(box, other))) continue;
      label.visible = true; placed.push(box);
    }
    return Promise.resolve();
  };
  return () => { grid.update = nativeUpdate; grid.createLabel = nativeCreateLabel; };
}
