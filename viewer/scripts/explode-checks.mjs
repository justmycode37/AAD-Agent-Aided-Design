// Browser integration checks for the public engine animation and transformed parents.
import { createHash } from "node:crypto";
import { gzipSync, gunzipSync } from "node:zlib";

export function concentricFixture(fixture) {
  const scene = JSON.parse(gunzipSync(fixture.blob));
  const root = scene.shapes ?? scene;
  const first = root.parts[0];
  root.parts = ["inner", "middle", "outer"].map((name, i) => ({
    ...structuredClone(first), id: `/Group/${name}`, name,
    color: ["#e8b024", "#5599cc", "#cc6655"][i],
  }));
  const blob = gzipSync(JSON.stringify(scene));
  return { blob, manifest: { ...fixture.manifest,
    scene_id: createHash("sha256").update(blob).digest("hex"), total_bytes: blob.length,
    triangles: fixture.manifest.triangles * 3,
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
  await frame.evaluate(() => globalThis.__viewer.setExplode(false));
  together = await snapshot();
  check(together.every((part, i) => same(part.position, assembled[i].position)), "native CAD collapse restores all component positions");
  check(await frame.evaluate(() => globalThis.__aad.loads === 1 && globalThis.__aad.verified), "exploded layout reuses the verified original scene");
}
