/** Screen-space density for the pinned CAD engine's grid; model geometry is untouched. */
// The native texture uses an 80px font in a 128px canvas: 18px sprites yield ~11px text.
const LABEL_HEIGHT = 18;
const TARGET_SPACING = 85;
export interface LabelBox { x: number; y: number; width: number; height: number }

/** Choose sparse subdivisions of the fixed grid, with a dead band around zoom thresholds. */
export function gridDivisions(halfExtent: number, pixelsPerUnit: number, previous = 0): number {
  const spacing = halfExtent * pixelsPerUnit / previous;
  if (previous && spacing >= 64 && spacing <= 150) return previous;
  const candidates = [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 25, 32, 40, 50, 64, 80, 100, 128];
  const readable = candidates.filter(n => {
    const value = halfExtent / n;
    return Math.abs(Number(value.toPrecision(4)) - value) <= Math.abs(value) * 1e-8;
  });
  const wanted = halfExtent * pixelsPerUnit / TARGET_SPACING;
  return readable.filter(n => n <= wanted).at(-1) ?? 1;
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
  let previousDivisions = 0;
  let visibleLastFrame = new Set<any>();
  grid.createLabel = function(_tick: string, x: number, plane: number, horizontal: boolean) {
    const text = gridCoordinate(x, 0);
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
    // Density depends on camera scale, never orbit angle or a snapped camera target.
    const cameraTarget = grid.position.clone().set(...viewer.getCameraTarget());
    const distance = cameraTarget.distanceTo(camera.position);
    const pixelsPerUnit = height * Math.abs(camera.projectionMatrix.elements[5]) /
      (2 * (camera.isPerspectiveCamera ? Math.max(distance, 1e-12) : 1));
    const divisions = gridDivisions(halfExtent, pixelsPerUnit, previousDivisions);
    const key = `${divisions}:${grid.theme}`;
    if (key !== previousKey) {
      // Dispose grid lines and cached label resources before rebuilding; no scene meshes are involved.
      for (const group of grid.children) group.children[0]?.traverse((object: any) => {
        object.geometry?.dispose();
        for (const material of [object.material ?? []].flat()) material.dispose();
      });
      grid.clearCache(); grid.clear();
      // Rebuild subdivisions only; never resize or recenter the grid box.
      grid.size = 2 * halfExtent;
      grid.ticks = divisions;
      void grid.create(false);
      previousKey = key;
      previousDivisions = divisions;
      visibleLastFrame.clear();
    }
    if (force) grid.setVisible();
    // Keep the original planes, extent and origin fixed in world coordinates, as in native CAD.
    grid.setCenter(grid.getAxes0(), grid.flipY);
    grid.updateWorldMatrix(true, true);
    const labelHeight = LABEL_HEIGHT * grid.gridFontSize / 10;
    const labels: { label: any; box: LabelBox; value: number }[] = [];
    for (const group of grid.children) {
      for (const label of group.children.slice(1)) {
        label.visible = false;
        if (!group.visible || !label.isSprite) continue;
        // Keep coordinates at a constant, modest CSS pixel size.
        // Preserve the native axis orientation from the reference viewer.
        const aspect = label.userData.aspectRatio || 4;
        const scale = 2 * labelHeight / (height * Math.abs(camera.projectionMatrix.elements[5]));
        label.scale.set(scale * aspect, scale, 1);
        const point = label.getWorldPosition(label.position.clone()).project(camera);
        const box = { x: (point.x + 1) * width / 2, y: (1 - point.y) * height / 2, width: labelHeight * (aspect * Math.abs(Math.cos(label.material.rotation)) + Math.abs(Math.sin(label.material.rotation))), height: labelHeight * (aspect * Math.abs(Math.sin(label.material.rotation)) + Math.abs(Math.cos(label.material.rotation))) };
        if (![point.x, point.y, point.z].every(Number.isFinite) || point.z < -1 || point.z > 1) continue;
        if (box.x - box.width / 2 < 8 || box.x + box.width / 2 > width - 8 || box.y - box.height / 2 < 64 || box.y + box.height / 2 > height - 16) continue;
        if (box.x < 100 && box.y > height - 110) continue; // XYZ orientation marker.
        labels.push({ label, box, value: Math.abs(label.userData.aadTick ?? 0) });
      }
    }
    // Important small coordinates win; suppress duplicate/overlapping labels across all three planes.
    labels.sort((a, b) => Number(visibleLastFrame.has(b.label)) - Number(visibleLastFrame.has(a.label)) || a.value - b.value);
    visibleLastFrame = new Set();
    const placed: LabelBox[] = [];
    for (const { label, box } of labels) {
      if (placed.some(other => overlaps(box, other))) continue;
      label.visible = true; placed.push(box); visibleLastFrame.add(label);
    }
    return Promise.resolve();
  };
  return () => { grid.update = nativeUpdate; grid.createLabel = nativeCreateLabel; };
}
