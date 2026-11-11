// Browser checks for trackpad navigation, driven by real wheel and mouse input through a reference-host page.
// Platform is overridden in the viewer's frame so these run the same on Mac and Linux CI.
const SETTLE_MS = 120;

const state = (frame) => frame.evaluate(() => {
  const v = globalThis.__viewer;
  const r = v.rendered;
  const canvas = r.controls.domElement;
  return {
    position: v.getCameraPosition(), quaternion: v.getCameraQuaternion(), zoom: v.getCameraZoom(),
    target: v.getCameraTarget(), wrapperTarget: r.camera.target.toArray(),
    height: canvas.clientHeight, width: canvas.clientWidth,
  };
});

/** Where a fixed world point lands on the canvas, in CSS pixels from the center (y down). */
const project = (frame, point) => frame.evaluate((point) => {
  const r = globalThis.__viewer.rendered, canvas = r.controls.domElement, camera = r.camera.getCamera();
  camera.updateMatrixWorld();
  const p = r.controls.getTarget().clone().set(...point).project(camera);
  return [p.x * canvas.clientWidth / 2, -p.y * canvas.clientHeight / 2];
}, point);

const near = (a, b, tolerance = 1e-6) => a.length === b.length && a.every((n, i) => Math.abs(n - b[i]) < tolerance);
const closeTo = (a, b, tolerance) => Math.abs(a - b) <= tolerance;

/**
 * @param {object} o
 * @param {import("playwright").Page} o.page the host page
 * @param {import("playwright").Frame} o.frame the viewer's frame
 * @param {(ok: boolean, label: string, detail?: string) => void} o.check
 * @param {string} [o.label] prefixes each check
 */
export async function checkNavigation({ page, frame, check, label = "navigation" }) {
  const setPlatform = (platform) => frame.evaluate((platform) => {
    Object.defineProperty(navigator, "platform", { configurable: true, get: () => platform });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, get: () => 0 });
  }, platform);
  const center = await frame.evaluate(() => {
    const rect = globalThis.__viewer.rendered.controls.domElement.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  });
  const offset = await page.evaluate(() => {
    const rect = document.getElementById("f").getBoundingClientRect();
    return { x: rect.left, y: rect.top };
  });
  const x = center.x + offset.x, y = center.y + offset.y;
  const hitsCanvas = await frame.evaluate(({ x, y }) => document.elementFromPoint(x, y) === globalThis.__viewer.rendered.controls.domElement, center);
  check(hitsCanvas, `${label}: the input point is on the CAD canvas`);
  await page.mouse.move(x, y);
  const wheel = async (dx, dy, modifier) => {
    if (modifier) await page.keyboard.down(modifier);
    await page.mouse.wheel(dx, dy);
    if (modifier) await page.keyboard.up(modifier);
    await page.waitForTimeout(SETTLE_MS);
    await frame.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  };

  const button = (name) => frame.getByRole("button", { name, exact: true });
  await button("Reset").click();
  await page.mouse.move(x, y);
  const home = await state(frame);

  await setPlatform("MacIntel");
  for (const [name, dx, dy] of [["left", 60, 0], ["right", -60, 0], ["up", 0, 40], ["down", 0, -40], ["diagonally", 45, -35]]) {
    const before = await state(frame);
    const anchor = await project(frame, before.target);
    await wheel(dx, dy);
    const after = await state(frame);
    const moved = await project(frame, before.target);
    const screen = [moved[0] - anchor[0], moved[1] - anchor[1]];
    check(closeTo(screen[0], -dx, 0.5) && closeTo(screen[1], -dy, 0.5), `${label}: Mac two-finger scroll moves content ${name} with the fingers`, screen.map(n => n.toFixed(2)).join(","));
    check(near(after.quaternion, before.quaternion) && closeTo(after.zoom, before.zoom, 1e-6), `${label}: pan ${name} keeps rotation and zoom`);
    const shift = after.target.map((n, i) => n - before.target[i]);
    check(!near(shift, [0, 0, 0]) && near(after.wrapperTarget.map((n, i) => n - before.wrapperTarget[i]), shift) && near(after.position.map((n, i) => n - before.position[i]), shift), `${label}: pan ${name} moves the camera and both lookAt targets together`);
  }

  let before = await state(frame);
  await wheel(0, -20, "Control");
  let after = await state(frame);
  check(closeTo(after.zoom / before.zoom, Math.exp(0.2), 1e-3), `${label}: pinch zooms once, exponentially`, (after.zoom / before.zoom).toFixed(4));
  check(near(after.target, before.target) && near(after.wrapperTarget, before.wrapperTarget), `${label}: pinch does not pan`);

  before = await state(frame);
  await wheel(0, -60, "Alt");
  after = await state(frame);
  check(after.zoom > before.zoom, `${label}: Option-scroll zooms on a Mac`, `${before.zoom} -> ${after.zoom}`);
  check(near(after.target, before.target), `${label}: Option-scroll does not pan`);

  await setPlatform("Win32");
  before = await state(frame);
  await wheel(0, -60);
  after = await state(frame);
  check(after.zoom > before.zoom && near(after.target, before.target), `${label}: an ordinary wheel keeps native zoom off a Mac`, `${before.zoom} -> ${after.zoom}`);
  before = await state(frame);
  await wheel(0, -20, "Control");
  after = await state(frame);
  check(closeTo(after.zoom / before.zoom, Math.exp(0.2), 1e-3) && near(after.target, before.target), `${label}: pinch zooms off a Mac too`);
  await setPlatform("MacIntel");

  await frame.evaluate(() => globalThis.__viewer.setOrtho(true));
  before = await state(frame);
  const orthoAnchor = await project(frame, before.target);
  await wheel(35, -25);
  after = await state(frame);
  const orthoMoved = await project(frame, before.target);
  check(closeTo(orthoMoved[0] - orthoAnchor[0], -35, 0.5) && closeTo(orthoMoved[1] - orthoAnchor[1], 25, 0.5) && closeTo(after.zoom, before.zoom, 1e-9), `${label}: orthographic pan follows the fingers`);
  before = after;
  await wheel(0, -20, "Control");
  after = await state(frame);
  check(closeTo(after.zoom / before.zoom, Math.exp(0.2), 1e-3) && near(after.position, before.position), `${label}: orthographic pinch zooms without moving the camera`);
  await frame.evaluate(() => globalThis.__viewer.setOrtho(false));

  const drag = async (fn) => {
    const start = await state(frame);
    await page.mouse.move(x, y);
    await fn();
    await page.waitForTimeout(SETTLE_MS);
    return [start, await state(frame)];
  };
  [before, after] = await drag(async () => {
    await page.mouse.down();
    await page.mouse.move(x + 50, y + 20, { steps: 4 });
    await page.mouse.up();
  });
  check(!near(after.quaternion, before.quaternion, 1e-3) && near(after.target, before.target), `${label}: click-drag still rotates`);
  [before, after] = await drag(async () => {
    await page.keyboard.down("Shift");
    await page.mouse.down();
    await page.mouse.move(x + 40, y + 10, { steps: 4 });
    await page.mouse.up();
    await page.keyboard.up("Shift");
  });
  check(near(after.quaternion, before.quaternion, 1e-6) && !near(after.target, before.target), `${label}: Shift-drag still pans`);
  [before, after] = await drag(async () => {
    await page.mouse.down({ button: "right" });
    await page.mouse.move(x - 30, y + 25, { steps: 4 });
    await page.mouse.up({ button: "right" });
  });
  check(near(after.quaternion, before.quaternion, 1e-6) && !near(after.target, before.target), `${label}: right-drag still pans`);

  if (await button("Pull apart").isEnabled()) {
    await button("Reset").click();
    await page.mouse.move(x, y);
    await wheel(30, 20);
    const assembled = await state(frame);
    await button("Pull apart").click();
    await page.mouse.move(x, y);
    await wheel(-50, 25);
    await button("Put together").click();
    // The animated transition can outlast a fixed wait on slow software-GL CI runners.
    const restored = (view) => near(view.position, assembled.position) && near(view.target, assembled.target) && near(view.wrapperTarget, assembled.wrapperTarget) && closeTo(view.zoom, assembled.zoom, 1e-6);
    for (let waited = 0; waited < 3000; waited += 100) {
      after = await state(frame);
      if (restored(after)) break;
      await frame.waitForTimeout(100);
    }
    check(restored(after), `${label}: put together restores the assembled view after panning while apart`);
    await wheel(40, 0);
  }

  await button("Reset").click();
  after = await state(frame);
  check(near(after.position, home.position) && near(after.target, home.target) && near(after.wrapperTarget, home.wrapperTarget) && near(after.quaternion, home.quaternion) && closeTo(after.zoom, home.zoom, 1e-6), `${label}: Reset undoes panning`);
  await frame.evaluate(() => globalThis.__viewer.reset());
  const nativeHome = await state(frame);
  await page.mouse.move(x, y);
  await wheel(300, 150);
  await frame.evaluate(() => globalThis.__viewer.reset());
  after = await state(frame);
  check(near(after.position, nativeHome.position) && near(after.target, nativeHome.target) && near(after.wrapperTarget, after.target) && closeTo(after.zoom, nativeHome.zoom, 1e-6), `${label}: native CAD Reset restores both camera targets and zoom after a pan`);
  await frame.evaluate(() => globalThis.__viewer.setOrtho(true));
  check(closeTo((await state(frame)).zoom, nativeHome.zoom, 1e-6), `${label}: projection switching after native Reset keeps the restored zoom`);
  await frame.evaluate(() => globalThis.__viewer.setOrtho(false));
  await frame.evaluate(() => { delete navigator.platform; delete navigator.maxTouchPoints; });
}
