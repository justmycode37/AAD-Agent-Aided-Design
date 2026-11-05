import { describe, expect, it } from "vitest";
import { cameraFit } from "../src/camera-fit";

const corners = (size: number[]) => [-1, 1].flatMap(x => [-1, 1].flatMap(y => [-1, 1].map(z => [x * size[0] / 2, y * size[1] / 2, z * size[2] / 2])));

describe("camera framing", () => {
  for (const ortho of [false, true]) for (const width of [240, 390, 728, 1200]) for (const size of [[10, 40, 1200], [1200, 40, 10], [200, 200, 200]]) {
    it(`${ortho ? "orthographic" : "perspective"} fits ${size.join("x")} in ${width}px`, () => {
      const height = 476, distance = 1000, near = .1;
      const points = corners(size);
      const scaleY = ortho ? .003 : 1 / Math.tan(11 * Math.PI / 180);
      const scaleX = scaleY * height / width;
      const fit = cameraFit(points, { width, height, scaleX, scaleY, ortho, distance, near, verticalPadding: 56 });
      for (const [x, y, z] of points) {
        const divisor = ortho ? 1 / fit.zoomFactor : fit.distance - z;
        const px = (x * scaleX / divisor + 1) * width / 2;
        const py = (1 - y * scaleY / divisor) * height / 2;
        expect(px).toBeGreaterThanOrEqual(24 - 1e-7);
        expect(px).toBeLessThanOrEqual(width - 24 + 1e-7);
        expect(py).toBeGreaterThanOrEqual(56 - 1e-7);
        expect(py).toBeLessThanOrEqual(height - 56 + 1e-7);
        expect(fit.distance - z).toBeGreaterThan(near);
      }
    });
  }
});
