/** Check projected world bounds rather than just a screenshot's center pixel. */
export async function checkFraming(frame, check, label, verticalPadding = 56) {
  const result = await frame.evaluate(() => {
    const v = globalThis.__viewer, camera = v.rendered.camera.getCamera();
    const canvas = v.rendered.controls.domElement;
    camera.updateMatrixWorld(true);
    const points = [];
    for (const [id, state] of Object.entries(v.getStates())) {
      if (!state.includes(1)) continue;
      const b = v.bbox.clone().makeEmpty().expandByObject(v.rendered.nestedGroup.groups[id], false);
      for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) {
        const p = b.min.clone().set(x, y, z).project(camera);
        points.push([(p.x + 1) * canvas.clientWidth / 2, (1 - p.y) * canvas.clientHeight / 2, p.z]);
      }
    }
    return { width: canvas.clientWidth, height: canvas.clientHeight,
      minX: Math.min(...points.map(p => p[0])), maxX: Math.max(...points.map(p => p[0])),
      minY: Math.min(...points.map(p => p[1])), maxY: Math.max(...points.map(p => p[1])),
      depthFits: points.every(p => p[2] > -1 && p[2] < 1) };
  });
  check(result.minX >= 23.9 && result.maxX <= result.width - 23.9 && result.minY >= verticalPadding - .1 && result.maxY <= result.height - verticalPadding + .1 && result.depthFits,
    `framing: ${label} clears every edge and the title controls`, JSON.stringify(result));
}
