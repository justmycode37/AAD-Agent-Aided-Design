// Browser integration checks for the public engine animation and transformed parents.
import { createHash } from "node:crypto";
import { gzipSync, gunzipSync } from "node:zlib";

export function concentricFixture(fixture, count = 3, asymmetric = false) {
  const scene = JSON.parse(gunzipSync(fixture.blob));
  const root = scene.shapes ?? scene;
  const first = root.parts[0];
  root.parts = Array.from({ length: count }, (_, i) => {
    const name = ["inner", "middle", "outer"][i] ?? `part-${i}`;
    const part = {
    ...structuredClone(first), id: `/Group/${name}`, name,
    color: ["#e8b024", "#5599cc", "#cc6655"][i % 3],
    };
    if (asymmetric && i >= 2) { part.loc[0][0] += (i - 1) * 75; part.loc[0][1] += (i - 2) * 20; }
    return part;
  });
  if (asymmetric) { root.bb.xmax += (count - 2) * 75; root.bb.ymax += (count - 3) * 20; }
  const blob = gzipSync(JSON.stringify(scene));
  return { blob, manifest: { ...fixture.manifest,
    scene_id: createHash("sha256").update(blob).digest("hex"), total_bytes: blob.length,
    triangles: fixture.manifest.triangles * count,
    parts: root.parts.map(({ id, name }) => ({ path: id, name })),
  } };
}

export async function checkExplode({ frame, check }) {
  const snapshot = () => frame.evaluate(() => {
    const v = globalThis.__viewer;
    v.rendered.nestedGroup.rootGroup.updateMatrixWorld(true);
    return Object.entries(v.getStates()).map(([id, state]) => {
      const group = v.rendered.nestedGroup.groups[id];
      const box = v.bbox.clone().makeEmpty().expandByObject(group, false);
      const geometry = [];
      group.traverse(object => { if (object.geometry) geometry.push(object.geometry.uuid); });
      return { id, visible: state.includes(1), position: group.position.toArray(),
        quaternion: group.quaternion.toArray(), scale: group.scale.toArray(), geometry,
        min: box.min.toArray(), max: box.max.toArray() };
    });
  });
  const overlaps = parts => parts.some((a, i) => parts.slice(i + 1).some(b => [0, 1, 2].every(k => Math.min(a.max[k], b.max[k]) - Math.max(a.min[k], b.min[k]) > 1e-5)));
  const same = (a, b) => a.length === b.length && a.every((x, i) => Math.abs(x - b[i]) < 1e-5);
  await frame.evaluate(() => {
    const root = globalThis.__viewer.rendered.nestedGroup.rootGroup;
    root.position.set(12, -7, 5);
    root.rotation.set(.2, .5, -.3);
    root.scale.set(1.1, .8, 1.2);
    root.updateMatrixWorld(true);
  });
  const assembled = await snapshot();
  check(overlaps(assembled), "explode regression starts with concentric overlapping bounds");
  await frame.getByRole("button", { name: "Pull apart", exact: true }).click();
  let apart = await snapshot();
  check(!overlaps(apart), "pull apart clears concentric bounds under a rotated, scaled parent");
  check(apart.every((part, i) => same(part.quaternion, assembled[i].quaternion) && same(part.scale, assembled[i].scale) && JSON.stringify(part.geometry) === JSON.stringify(assembled[i].geometry)), "explode translates original geometry without rotating, scaling, or replacing it");
  await frame.getByRole("button", { name: "Put together", exact: true }).click();
  let together = await snapshot();
  check(together.every((part, i) => same(part.position, assembled[i].position) && same(part.min, assembled[i].min) && same(part.max, assembled[i].max)), "put together restores original transforms and world bounds");

  const hidden = assembled[0].id;
  await frame.evaluate(id => globalThis.__viewer.setState(id, [0, 0]), hidden);
  // Exercise the same public API used by the native CAD controls.
  await frame.evaluate(() => { const v = globalThis.__viewer; v.setExplode(true); v.setRelativeTime(1); });
  apart = await snapshot();
  check(!overlaps(apart.filter(part => part.visible)), "native CAD explode uses the same separated layout");
  check(same(apart.find(part => part.id === hidden).position, assembled.find(part => part.id === hidden).position), "explode leaves hidden parts in place");
  await frame.evaluate(id => globalThis.__viewer.setState(id, [1, 1]), hidden);
  apart = await snapshot();
  check(!overlaps(apart), "re-showing a part while exploded replans a clear layout");
  check(await frame.evaluate(() => globalThis.__viewer.getRelativeTime() > .999), "visibility replanning preserves the exploded animation time");
  await frame.evaluate(() => { const v = globalThis.__viewer; v.setRelativeTime(.4); const states = v.getStates(); v.setStates(Object.fromEntries(Object.keys(states).map(id => [id, [0, 0]]))); });
  check(await frame.evaluate(() => globalThis.__viewer.state.get("animationMode") === "explode" && Math.abs(globalThis.__viewer.getRelativeTime() - .4) < 1e-6), "hiding every part preserves explode mode and a partial animation time");
  await frame.evaluate(() => { const v = globalThis.__viewer; v.setStates(Object.fromEntries(Object.keys(v.getStates()).map(id => [id, [1, 1]]))); v.setRelativeTime(1); });
  check(!overlaps(await snapshot()), "showing all parts again regenerates the complete exploded layout");
  await frame.evaluate(() => globalThis.__viewer.setExplode(false));
  together = await snapshot();
  check(together.every((part, i) => same(part.position, assembled[i].position)), "native CAD collapse restores all component positions");
  check(await frame.evaluate(() => globalThis.__aad.loads === 1 && globalThis.__aad.verified), "exploded layout reuses the verified original scene");
}

export async function checkExplodedCamera({ frame, check, large = false }) {
  const cameraState = () => frame.evaluate(() => {
    const v = globalThis.__viewer, camera = v.rendered.camera;
    return { quaternion: v.getCameraQuaternion(), far: [camera.pCamera.far, camera.oCamera.far], target: v.getCameraTarget(), wrapperTarget: camera.target.toArray() };
  });
  const same = (a, b) => a.every((x, i) => Math.abs(x - b[i]) < 1e-7);
  const before = await cameraState();
  await frame.getByRole("button", { name: "Pull apart", exact: true }).click();
  const apart = await cameraState();
  check(same(before.quaternion, apart.quaternion), "fitting an asymmetric exploded layout preserves view direction");
  check(same(apart.target, apart.wrapperTarget), "exploded fitting keeps both camera targets synchronized");
  if (large) {
    const visible = await frame.evaluate(() => {
      const v = globalThis.__viewer, camera = v.rendered.camera.getCamera();
      camera.updateMatrixWorld(true);
      return Object.keys(v.getStates()).every(id => {
        const b = v.bbox.clone().makeEmpty().expandByObject(v.rendered.nestedGroup.groups[id], false);
        const point = b.min.clone().add(b.max).multiplyScalar(.5).project(camera);
        return Math.abs(point.x) < 1 && Math.abs(point.y) < 1 && point.z > -1 && point.z < 1;
      });
    });
    check(apart.far.every((far, i) => far > before.far[i]), "large exploded stacks extend both camera far planes");
    check(visible, "all forty exploded component centers remain inside the camera frustum");
  }
  await frame.getByRole("button", { name: "Put together", exact: true }).click();
  check(same((await cameraState()).far, before.far), "collapse restores the original camera clipping range");
}
