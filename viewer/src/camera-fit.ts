/** Fit camera-oriented bounds to the canvas, with room for the chat controls. */
export function cameraFit(
  points: readonly (readonly number[])[],
  options: { width: number; height: number; scaleX: number; scaleY: number; ortho: boolean; distance: number; near: number; verticalPadding: number },
): { distance: number; zoomFactor: number } {
  const { width, height, scaleX, scaleY, ortho, distance, near, verticalPadding } = options;
  const limitX = Math.max(.1, 1 - 48 / Math.max(1, width));
  const limitY = Math.max(.1, 1 - 2 * verticalPadding / Math.max(1, height));
  let required = near * 2;
  let factor = Infinity;
  for (const [x, y, z] of points) {
    required = Math.max(required, z + near * 2);
    if (ortho) {
      if (x) factor = Math.min(factor, limitX / Math.abs(x * scaleX));
      if (y) factor = Math.min(factor, limitY / Math.abs(y * scaleY));
    } else {
      required = Math.max(required, z + Math.abs(x * scaleX) / limitX, z + Math.abs(y * scaleY) / limitY);
    }
  }
  return ortho
    ? { distance: Math.max(distance, required), zoomFactor: Number.isFinite(factor) ? factor : 1 }
    : { distance: required, zoomFactor: distance / required };
}
