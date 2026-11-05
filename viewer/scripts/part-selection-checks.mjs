// Browser checks for chat part selection: real mouse input against the real engine, GPU picker and shaders.
//   node scripts/part-selection-checks.mjs   builds its own page, without touching dist/
// checkPartSelection() also runs in a host page that exposes globalThis.__viewer, __view and
// __partSelection = { selection, selections: string[][], references: string[] }.
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";

const SETTLE_MS = 60;
const SELECTED = 1, HOVER = 2; // The engine's HighlightFlag bits.

/** Three instances of one shape in a row, plus a fourth parked by the checks in front of the middle one. */
export function selectionScene(fixture) {
  const scene = JSON.parse(gunzipSync(fixture.blob));
  const root = scene.shapes ?? scene;
  const first = root.parts[0];
  root.parts = ["left", "middle", "right", "shield"].map((name, i) => {
    const part = { ...structuredClone(first), id: `/Group/${name}`, name, color: ["#e8b024", "#5599cc", "#cc6655", "#66aa55"][i] };
    part.loc[0][0] = [-90, 0, 90, 0][i];
    return part;
  });
  root.bb.xmin -= 90; root.bb.xmax += 90;
  return scene;
}

const settle = async (page, frame) => {
  await page.waitForTimeout(SETTLE_MS);
  await frame.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
};

const scan = (frame) => frame.evaluate(() => {
  const v = globalThis.__viewer, canvas = v.renderer.domElement, rect = canvas.getBoundingClientRect();
  v.idPicker.setDirty();
  const faces = {};
  let blank = null;
  for (let y = 12; y < rect.height - 12; y += 8) for (let x = 12; x < rect.width - 12; x += 8) {
    const read = (dx, dy) => v.idPicker.pickAt(x + dx, y + dy, { windowSize: 1 });
    const hit = read(0, 0);
    if (!hit) { if (!blank && [[-8, -8], [8, -8], [-8, 8], [8, 8]].every(([dx, dy]) => !read(dx, dy))) blank = [x + rect.left, y + rect.top]; continue; }
    if (hit.info.topo !== "face" || !v.pickingController.pickVisible(hit.info)) continue;
    // Only a pixel well inside a face, so a one-pixel error cannot land on an edge or another face.
    if (![[-6, 0], [6, 0], [0, -6], [0, 6]].every(([dx, dy]) => read(dx, dy)?.id === hit.id)) continue;
    const part = faces[hit.info.solidPath] ??= {};
    (part[hit.id] ??= []).push([x + rect.left, y + rect.top]);
  }
  const parts = {};
  for (const [path, ids] of Object.entries(faces)) {
    parts[path] = Object.entries(ids).sort((a, b) => b[1].length - a[1].length)
      .map(([id, points]) => ({ id: +id, point: points[points.length >> 1] }));
  }
  return { parts, blank };
});

const flagged = (frame, bit) => frame.evaluate((bit) => {
  const nested = globalThis.__viewer.rendered.nestedGroup, ids = [];
  for (const info of nested.registry.entries()) if (nested.highlight.data[info.id] & bit) ids.push(info.id);
  return ids;
}, bit);

const partIds = (frame, path) => frame.evaluate((path) => {
  const ids = { face: [], edge: [], vertex: [] };
  for (const info of globalThis.__viewer.rendered.nestedGroup.registry.entries()) if (info.solidPath === path) ids[info.topo].push(info.id);
  return ids;
}, path);

/** Everything that defines the original geometry and its materials. */
const geometryState = (frame) => frame.evaluate(() => {
  const sum = array => { let h = 2166136261; for (const byte of new Uint8Array(array.buffer, array.byteOffset, array.byteLength)) h = Math.imul(h ^ byte, 16777619); return h >>> 0; };
  const rows = [];
  let materials = 0;
  globalThis.__viewer.rendered.nestedGroup.rootGroup.traverse(object => {
    if (object.material) materials++;
    const g = object.geometry;
    if (!g) return;
    const attributes = Object.keys(g.attributes).filter(name => g.attributes[name].array).map(name => `${name}:${sum(g.attributes[name].array)}`);
    rows.push([object.name, object.type, object.uuid, attributes.join(","), g.index ? sum(g.index.array) : 0, object.matrixWorld.elements.join(",")].join("|"));
  });
  return { rows: rows.join("\n"), materials, uuids: (() => { const out = []; globalThis.__viewer.rendered.nestedGroup.rootGroup.traverse(o => { if (o.material) out.push([o.material].flat().map(m => m.uuid).join()); }); return out.join(); })() };
});

const events = (frame) => frame.evaluate(() => ({ selections: globalThis.__partSelection.selections.length, references: globalThis.__partSelection.references.length, paths: globalThis.__partSelection.selection.paths(), lastReference: globalThis.__partSelection.references.at(-1) }));

export async function checkPartSelection({ page, frame, check, offset = { x: 0, y: 0 }, label = "part selection" }) {
  const at = ([x, y]) => [x + offset.x, y + offset.y];
  const move = async (point) => { await page.mouse.move(...at(point)); await settle(page, frame); };
  const click = async (point, options) => { await page.mouse.click(...at(point), options); await settle(page, frame); };
  const pixel = async (point) => {
    const [x, y] = at(point);
    const png = await page.screenshot({ clip: { x, y, width: 1, height: 1 } });
    return page.evaluate(async (base64) => {
      const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${base64}`)).blob());
      const canvas = new OffscreenCanvas(1, 1), context = canvas.getContext("2d");
      context.drawImage(bitmap, 0, 0);
      return [...context.getImageData(0, 0, 1, 1).data.slice(0, 3)];
    }, png.toString("base64"));
  };
  const differs = (a, b, by = 12) => a.some((n, i) => Math.abs(n - b[i]) > by);
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const sorted = (ids) => [...ids].sort((a, b) => a - b);

  const canvasOk = await frame.evaluate(() => globalThis.__viewer.renderer.domElement === globalThis.__viewer.rendered.controls.domElement);
  check(canvasOk, `${label}: the renderer canvas receives navigation input`);
  await frame.evaluate(() => { const v = globalThis.__viewer; v.setState("/Group/shield", [0, 0]); });
  await page.mouse.move(...at([1, 1]));
  await settle(page, frame);
  const original = await geometryState(frame);
  const { parts, blank } = await scan(frame);
  const LEFT = "/Group/left", MIDDLE = "/Group/middle", RIGHT = "/Group/right";
  const faces = parts[MIDDLE] ?? [];
  check(faces.length >= 2 && !!parts[LEFT] && !!parts[RIGHT] && !!blank && !parts["/Group/shield"], `${label}: the scene shows several faces of three instanced parts, and a hidden part is not pickable`, JSON.stringify(Object.keys(parts)));
  const [faceA, faceB] = faces;
  const leftPoint = parts[LEFT][0].point, rightPoint = parts[RIGHT][0].point;
  const whole = await partIds(frame, MIDDLE);
  const wholeIds = sorted([...whole.face, ...whole.edge]);
  check(whole.face.length > 1 && whole.edge.length > 1, `${label}: the middle part has several faces and edges`, `${whole.face.length}/${whole.edge.length}`);
  const leftIds = await partIds(frame, LEFT);
  check(!leftIds.face.some(id => whole.face.includes(id)), `${label}: instances of one shape have separate component ids`);

  const idleB = await pixel(faceB.point), idleLeft = await pixel(leftPoint);

  // Hover covers the whole part, whichever face is under the pointer.
  await move(faceA.point);
  check(same(sorted(await flagged(frame, HOVER)), wholeIds), `${label}: hovering one face flags all faces and edges of its part, and no corner points`);
  await page.screenshot({ path: "test-output/part-selection-hover.png" }).catch(() => {});
  const hoverB = await pixel(faceB.point);
  check(differs(hoverB, idleB), `${label}: hovering one face tints a different face of the part`, `${idleB} -> ${hoverB}`);
  check(same(await pixel(leftPoint), idleLeft), `${label}: hovering leaves other parts untinted`);
  await move(faceB.point);
  check(same(sorted(await flagged(frame, HOVER)), wholeIds), `${label}: moving across faces keeps the whole-part hover`);
  check((await events(frame)).selections === 0, `${label}: hovering does not select`);
  check(await frame.evaluate(() => globalThis.__viewer.pickingController.lastObject === null), `${label}: native face hover is off`);

  // Click selects the whole part, with a look that differs from hover.
  await click(faceA.point);
  let state = await events(frame);
  check(same(state.paths, [MIDDLE]) && state.selections === 1, `${label}: a click selects the owning part`, JSON.stringify(state));
  check(same(sorted(await flagged(frame, SELECTED)), wholeIds), `${label}: selection flags all faces and edges`);
  await move(leftPoint);
  await page.screenshot({ path: "test-output/part-selection-selected-left-hovered.png" }).catch(() => {});
  const selectedB = await pixel(faceB.point);
  check(differs(selectedB, idleB) && differs(selectedB, hoverB), `${label}: selected looks different from idle and hovered`, `${idleB} / ${hoverB} / ${selectedB}`);
  check(same(sorted(await flagged(frame, SELECTED)), wholeIds), `${label}: selection persists after the pointer leaves`);

  // Drags never change the selection.
  await page.mouse.move(...at(rightPoint));
  await page.mouse.down();
  await page.mouse.move(rightPoint[0] + offset.x + 40, rightPoint[1] + offset.y + 10, { steps: 4 });
  await page.mouse.move(...at(rightPoint), { steps: 4 });
  await page.mouse.up();
  await settle(page, frame);
  state = await events(frame);
  check(same(state.paths, [MIDDLE]) && state.selections === 1, `${label}: a drag back to its start does not select`, JSON.stringify(state));
  await page.mouse.down({ button: "right" });
  await page.mouse.up({ button: "right" });
  await settle(page, frame);
  check((await events(frame)).selections === 1, `${label}: a right press does not select`);

  const fresh = await scan(frame);
  const point = (path, n = 0) => fresh.parts[path][n].point;

  // Double-click selects and reports the part once.
  await page.mouse.dblclick(...at(point(LEFT)));
  await settle(page, frame);
  state = await events(frame);
  check(same(state.paths, [LEFT]) && state.references === 1 && state.lastReference === LEFT, `${label}: a double-click selects and references the part once`, JSON.stringify(state));
  await page.mouse.dblclick(...at(fresh.blank));
  await settle(page, frame);
  state = await events(frame);
  check(state.references === 1 && same(state.paths, []), `${label}: a double-click on background only clears`, JSON.stringify(state));
  for (let i = 0; i < 2; i++) {
    await page.mouse.move(...at(point(RIGHT)));
    await page.mouse.down({ clickCount: i + 1 });
    await page.mouse.move(point(RIGHT)[0] + offset.x + 12, point(RIGHT)[1] + offset.y, { steps: 3 });
    await page.mouse.move(...at(point(RIGHT)), { steps: 3 });
    await page.mouse.up({ clickCount: i + 1 });
  }
  await settle(page, frame);
  state = await events(frame);
  check(state.references === 1 && same(state.paths, []), `${label}: a double drag neither selects nor references`, JSON.stringify(state));

  const again = await scan(frame);
  const here = (path, n = 0) => again.parts[path][n].point;

  // Blank, Escape and Shift.
  await click(here(MIDDLE));
  await click(again.blank);
  check(same((await events(frame)).paths, []) && !(await flagged(frame, SELECTED)).length, `${label}: a blank click clears`);
  await click(here(MIDDLE));
  await click(here(LEFT), { modifiers: undefined });
  await page.keyboard.down("Shift");
  await click(here(RIGHT));
  await page.keyboard.up("Shift");
  check(same((await events(frame)).paths, [LEFT, RIGHT]), `${label}: Shift-click adds a part`);
  await frame.evaluate(() => { const input = document.createElement("input"); input.id = "selection-input"; document.body.append(input); input.focus(); });
  await page.keyboard.press("Escape");
  check((await events(frame)).paths.length === 2, `${label}: Escape in a text field leaves the selection`);
  await frame.evaluate(() => { document.getElementById("selection-input").remove(); document.body.focus(); });
  await page.keyboard.press("Escape");
  check(same((await events(frame)).paths, []), `${label}: Escape clears`);

  // Occlusion and hidden parts: park the fourth part between the camera and the middle one.
  const target = await frame.evaluate((path) => {
    const v = globalThis.__viewer, camera = v.rendered.camera.getCamera();
    const group = v.rendered.nestedGroup.groups[path];
    const world = group.localToWorld(camera.position.clone().set(20, -20, 25));
    const toCamera = camera.position.clone().sub(world).normalize().multiplyScalar(60);
    const shield = v.rendered.nestedGroup.groups["/Group/shield"];
    shield.position.copy(shield.parent.worldToLocal(world.clone().add(toCamera)));
    shield.updateMatrixWorld(true);
    v.setState("/Group/shield", [1, 1]);
    const p = world.clone().add(toCamera).project(camera), rect = v.renderer.domElement.getBoundingClientRect();
    v.update(true, false);
    return { x: rect.left + (p.x + 1) * rect.width / 2, y: rect.top + (1 - p.y) * rect.height / 2 };
  }, MIDDLE);
  const covered = (await scan(frame)).parts["/Group/shield"];
  const coveredPoint = covered?.find(face => Math.hypot(face.point[0] - target.x, face.point[1] - target.y) < 80)?.point ?? covered?.[0]?.point;
  check(!!coveredPoint, `${label}: the shield is pickable while shown`);
  await click(coveredPoint);
  check(same((await events(frame)).paths, ["/Group/shield"]), `${label}: a click takes the nearest visible part`);
  await frame.evaluate(() => globalThis.__viewer.setState("/Group/shield", [0, 0]));
  await settle(page, frame);
  check(same((await events(frame)).paths, []), `${label}: hiding the selected part drops it from the selection`);
  await click(coveredPoint);
  const through = (await events(frame)).paths;
  check(!through.includes("/Group/shield"), `${label}: a hidden part cannot be clicked`, JSON.stringify(through));
  await frame.evaluate(() => { const v = globalThis.__viewer, s = v.rendered.nestedGroup.groups["/Group/shield"]; s.position.set(0, 0, 0); s.updateMatrixWorld(true); });
  await frame.evaluate(() => globalThis.__partSelection.selection.clear());

  // Exploded parts pick where they are drawn, without a new scan to refresh the buffer.
  const exploded = await frame.evaluate((path) => {
    const v = globalThis.__viewer;
    globalThis.__view.setExploded(true);
    const camera = v.rendered.camera.getCamera(), rect = v.renderer.domElement.getBoundingClientRect();
    const local = camera.position.clone().set(20, camera.position.y < 0 ? -20 : -14, 25);
    v.rendered.nestedGroup.rootGroup.updateMatrixWorld(true);
    const p = v.rendered.nestedGroup.groups[path].localToWorld(local).project(camera);
    return [rect.left + (p.x + 1) * rect.width / 2, rect.top + (1 - p.y) * rect.height / 2];
  }, RIGHT);
  await settle(page, frame);
  await click(exploded);
  check(same((await events(frame)).paths, [RIGHT]), `${label}: a click on an exploded part selects it`, JSON.stringify(await events(frame)));
  await frame.evaluate(() => { globalThis.__view.setExploded(false); globalThis.__partSelection.selection.clear(); });
  await settle(page, frame);

  // A section: the cut face belongs to the cut part, and removed material is not pickable.
  const cut = await frame.evaluate(({ path, others }) => {
    const v = globalThis.__viewer, view = globalThis.__view;
    for (const other of others) v.setState(other, [0, 0]);
    const camera = v.rendered.camera.getCamera();
    const worldX = v.rendered.nestedGroup.groups[path].localToWorld(camera.position.clone().set(0, 0, 3)).x;
    const flip = camera.position.x < worldX ? -1 : 1;
    v.setActiveTab("clip");
    v.setClipNormal(0, [-flip, 0, 0], 0);
    v.refreshPlane(0, flip * worldX);
    for (const i of [1, 2]) v.refreshPlane(i, 1e4);
    v.update(true, false);
    const rect = v.renderer.domElement.getBoundingClientRect();
    const point = (x, y, z) => { const p = v.rendered.nestedGroup.groups[path].localToWorld(camera.position.clone().set(x, y, z)).project(camera); return [rect.left + (p.x + 1) * rect.width / 2, rect.top + (1 - p.y) * rect.height / 2]; };
    return { cap: point(0, 0, 3), removed: point(20, 15, 6) };
  }, { path: MIDDLE, others: [LEFT, RIGHT] });
  await settle(page, frame);
  await click(cut.cap);
  await page.screenshot({ path: "test-output/part-selection-section.png" }).catch(() => {});
  check(same((await events(frame)).paths, [MIDDLE]), `${label}: a click on a section's cut face selects the cut part`, JSON.stringify(await events(frame)));
  const sectionPicks = await frame.evaluate(() => globalThis.__viewer.idPicker.faceMaterial.side);
  check(sectionPicks === 2, `${label}: section picking includes the back faces the section draws`);
  await frame.evaluate(() => globalThis.__partSelection.selection.clear());
  await click(cut.removed);
  const afterRemoved = await events(frame);
  check(same(afterRemoved.paths, []), `${label}: a click where a section removed the material does not select it`, JSON.stringify(afterRemoved));
  await frame.evaluate(({ others }) => {
    const v = globalThis.__viewer;
    v.resetClip(); v.setActiveTab("tree");
    for (const other of others) v.setState(other, [1, 1]);
    globalThis.__partSelection.selection.clear();
  }, { others: [LEFT, RIGHT] });
  await settle(page, frame);

  // An analysis view: highlights work on its painted materials, and its colors come back.
  const painted = await scan(frame);
  const paintedPoint = painted.parts[MIDDLE][0].point;
  await move([1, 1]);
  await frame.evaluate(({ LEFT, MIDDLE, RIGHT }) => globalThis.__view.setAnalysis({ quantity: "q", units: "u", range: [0, 2], source: "test", values: [{ part: LEFT, value: 0 }, { part: MIDDLE, value: 1 }, { part: RIGHT, value: 2 }] }), { LEFT, MIDDLE, RIGHT });
  await frame.evaluate(() => globalThis.__viewer.update(true, false));
  await settle(page, frame);
  const analysisIdle = await pixel(paintedPoint);
  await click(paintedPoint);
  check(same((await events(frame)).paths, [MIDDLE]), `${label}: selection works in an analysis view`);
  await move([1, 1]);
  const analysisSelected = await pixel(paintedPoint);
  check(differs(analysisSelected, analysisIdle), `${label}: an analysis part shows its selection`, `${analysisIdle} -> ${analysisSelected}`);
  await frame.evaluate(() => globalThis.__partSelection.selection.clear());
  await settle(page, frame);
  check(!differs(await pixel(paintedPoint), analysisIdle, 2), `${label}: clearing restores the analysis color`);
  await frame.evaluate(() => { globalThis.__view.setAnalysis(); globalThis.__viewer.update(true, false); });
  await settle(page, frame);

  const final = await geometryState(frame);
  check(final.rows === original.rows, `${label}: geometry, ids and transforms are unchanged`);
  check(final.uuids === original.uuids, `${label}: visual materials are the originals, with no highlight clones`, `${original.materials} materials`);
}

async function main() {
  const { chromium } = await import("playwright");
  const { build } = await import("vite");
  const { loadFixture } = await import("./fake-mcp.mjs");
  const root = resolve(fileURLToPath(import.meta.url), "../..");
  const entry = "\0part-selection-page";
  const output = await build({
    root, configFile: false, logLevel: "warn",
    plugins: [{
      name: "part-selection-page",
      enforce: "pre",
      resolveId: id => id.endsWith("part-selection-page") ? entry : null,
      load: id => id !== entry ? null : `
        import { mountCad3d } from ${JSON.stringify(resolve(root, "src/viewer3d.ts"))};
        import { installPartSelection } from ${JSON.stringify(resolve(root, "src/part-selection.ts"))};
        globalThis.__mount = (shapes) => {
          const host = document.getElementById("host"), bounds = host.getBoundingClientRect();
          const view = mountCad3d(host, shapes, { width: bounds.width, height: bounds.height, theme: "light", controls: "simple" });
          view.fit();
          globalThis.__view = view; globalThis.__viewer = view.viewer;
          const record = { selections: [], references: [] };
          record.selection = installPartSelection(view.viewer, { onSelection: paths => record.selections.push(paths), onReference: path => record.references.push(path) });
          globalThis.__partSelection = record;
        };`,
    }],
    build: { write: false, minify: false, cssCodeSplit: false, target: "es2022", lib: { entry: "part-selection-page", formats: ["iife"], name: "PartSelectionPage", fileName: () => "page.js", cssFileName: "page" } },
  });
  const files = [output].flat().flatMap(result => result.output);
  const js = files.find(file => file.type === "chunk").code;
  const css = files.filter(file => file.fileName.endsWith(".css")).map(file => file.source).join("\n");

  const gpu = process.env.AAD_E2E_GPU === "1";
  const args = gpu ? ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"] : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
  const browser = await chromium.launch({ headless: true, channel: "chromium", args });
  let failures = 0;
  const check = (ok, label, detail = "") => {
    console.log(`${ok ? "ok  " : "FAIL"} ${label}${detail ? " " + detail : ""}`);
    if (!ok) failures++;
  };
  try {
    const page = await (await browser.newContext({ viewport: { width: 900, height: 620 } })).newPage();
    const logs = [];
    page.on("console", m => { if (m.type() === "error") logs.push(m.text().slice(0, 400)); });
    page.on("pageerror", e => logs.push(e.message));
    await page.setContent(`<!doctype html><html><head><style>${css}</style></head><body style="margin:0"><div id="host" style="width:900px;height:620px;position:relative"></div></body></html>`);
    await page.addScriptTag({ content: js });
    const scene = selectionScene(loadFixture(resolve(root, "tests/fixtures/bracket")));
    await page.evaluate(scene => globalThis.__mount(scene), scene);
    await page.waitForTimeout(300);
    await checkPartSelection({ page, frame: page.mainFrame(), check });

    // Disposal hands the same viewer back to native face hover.
    await page.evaluate(() => globalThis.__partSelection.selection.dispose());
    const nativeFace = await page.evaluate(() => {
      const v = globalThis.__viewer, rect = v.renderer.domElement.getBoundingClientRect();
      v.idPicker.setDirty();
      for (let y = 20; y < rect.height; y += 8) for (let x = 20; x < rect.width; x += 8) {
        const hit = v.idPicker.pickAt(x, y, { windowSize: 1 });
        if (hit?.info.topo === "face") return [rect.left + x, rect.top + y];
      }
      return null;
    });
    const before = await page.evaluate(() => globalThis.__partSelection.selections.length);
    await page.mouse.move(...nativeFace);
    await page.waitForTimeout(SETTLE_MS);
    await page.mouse.move(nativeFace[0] + 1, nativeFace[1]);
    await page.waitForTimeout(SETTLE_MS);
    const hover = await page.evaluate(() => {
      const nested = globalThis.__viewer.rendered.nestedGroup;
      let count = 0;
      for (const info of nested.registry.entries()) if (nested.highlight.data[info.id] & 2) count++;
      return { count, native: globalThis.__viewer.pickingController.lastObject !== null };
    });
    check(hover.native && hover.count === 1, "dispose: native single-face hover returns", JSON.stringify(hover));
    await page.mouse.click(...nativeFace);
    check(await page.evaluate(n => globalThis.__partSelection.selections.length === n, before), "dispose: clicks no longer select");
    check(logs.length === 0, "no console or page errors", logs.join(" | "));
    await page.screenshot({ path: resolve(root, "test-output/part-selection.png") }).catch(() => {});
  } finally {
    await browser.close();
  }
  if (failures) { console.error(`${failures} part selection checks failed`); process.exit(1); }
  console.log("part selection checks passed");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
