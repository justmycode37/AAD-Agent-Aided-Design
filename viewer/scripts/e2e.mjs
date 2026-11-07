// End-to-end viewer checks in a real browser through the reference host. Needs `npm run build` first.
// Set AAD_E2E_GPU=1 for real-GPU numbers on a Mac; the default software GL also runs in CI.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { deflateSync, gunzipSync, gzipSync } from "node:zlib";
import { createHash, randomBytes } from "node:crypto";
import { chromium } from "playwright";
import { openRefHost } from "./ref-host.mjs";
import { fakeMcp, loadFixture } from "./fake-mcp.mjs";
import { checkNavigation } from "./navigation-checks.mjs";
import { concentricFixture, checkExplode, checkExplodedCamera } from "./explode-checks.mjs";
import { checkFraming } from "./framing-checks.mjs";
import { checkUnavailableReference, checkComposerReference, checkRejectedComposerReference, checkUnsupportedExtensionShape, COMPOSER_CAPABILITIES } from "./part-reference-checks.mjs";

const OUT = "test-output";
mkdirSync(OUT, { recursive: true });
const html = readFileSync("dist/aad-viewer.html", "utf8");
const gpu = process.env.AAD_E2E_GPU === "1";
function pngChunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([type, data])));
  return Buffer.concat([len, type, data, crc]);
}
function crc32(buf) {
  let crc = 0xffffffff;
  for (const byte of buf) { crc ^= byte; for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
function pngData(bytes) {
  const width = 1024, height = Math.ceil(bytes.length / (width * 3));
  const padded = Buffer.alloc(width * height * 3); bytes.copy(padded);
  const rows = Buffer.alloc(height * (width * 3 + 1));
  for (let y = 0; y < height; y++) padded.copy(rows, y * (width * 3 + 1) + 1, y * width * 3, (y + 1) * width * 3);
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 2;
  return `data:image/png;base64,${Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), pngChunk(Buffer.from("IHDR"), ihdr), pngChunk(Buffer.from("IDAT"), deflateSync(rows, { level: 1 })), pngChunk(Buffer.from("IEND"), Buffer.alloc(0))]).toString("base64")}`;
}
const fixture = loadFixture("tests/fixtures/bracket");
let failures = 0;
const check = (ok, label, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${detail ? " " + detail : ""}`);
  if (!ok) failures++;
};

async function scenario(name, opts, fn) {
  const { fakeOpts, scene = fixture, viewer, ...hostOpts } = opts;
  const mcp = fakeMcp(scene, fakeOpts);
  if (viewer) mcp.toolResult.structuredContent.viewer = { mode: "inline", ...viewer };
  const host = await openRefHost({ html, toolResult: mcp.toolResult, onRequest: mcp.onRequest, gpu, ...hostOpts });
  try {
    const diag = await host.waitReady();
    check(diag.state === "ready", `${name}: reaches ready`, diag.error ?? "");
    await fn({ host, mcp, diag });
    check(host.requests.length === 0, `${name}: no network requests`, host.requests.join(" "));
    const bad = host.logs.filter((l) => /\[(error|pageerror|requestfailed)\]/.test(l) && !l.includes("injected failure"));
    check(bad.length === 0, `${name}: no console errors`, bad.join(" | "));
  } finally {
    await host.page.screenshot({ path: `${OUT}/${name}.png` }).catch(() => {});
    await host.close();
  }
}

/** A valid scene with enough incompressible-ish bytes to need many chunks. */
function bigScene(targetBytes) {
  const shapes = JSON.parse(gunzip(fixture.blob));
  const words = [];
  let x = 1;
  for (let length = 0; length < targetBytes * 1.6; length += words.at(-1).length) {
    x = (x * 1103515245 + 12345) & 0x7fffffff;
    words.push(x.toString(36));
  }
  const blob = gzipSync(JSON.stringify({ ...shapes, _padding: words.join("") }), { level: 1 });
  const scene_id = createHash("sha256").update(blob).digest("hex");
  return { manifest: { ...fixture.manifest, scene_id, total_bytes: blob.length }, blob };
}
function gunzip(b) { return gunzipSync(b).toString(); }

await scenario("small-part-t3-limits", {}, async ({ host, mcp, diag }) => {
  const state = await host.hostState();
  check(state.sized >= 500 && state.sized < 600, "requests its complete inline height", String(state.sized));
  check(state.tooLarge === 0, "no app message over 256 KiB");
  check(mcp.stats.chunks === 1, "one chunk for a small scene", String(mcp.stats.chunks));
  check(await host.frame().getByRole("button", { name: "Views", exact: true }).isDisabled(), "Views is disabled until the agent supplies recipes");
  const marker = await host.frame().evaluate(() => {
    const v = globalThis.__viewer, marker = v.rendered.orientationMarker;
    const calls = [], original = v.renderer.setViewport;
    v.renderer.setViewport = function (...args) { if (args.length === 4) calls.push(args); return original.apply(this, args); };
    try { v.update(true, false); } finally { v.renderer.setViewport = original; }
    marker.scene.updateMatrixWorld(true); marker.camera.updateMatrixWorld(true);
    const points = marker.labels.flatMap(label => {
      const b = label.geometry.boundingBox;
      return [b.min.x, b.max.x].flatMap(x => [b.min.y, b.max.y].map(y => {
        const point = label.position.clone().set(x, y, 0).applyMatrix4(label.matrixWorld).project(marker.camera);
        return [point.x, point.y];
      }));
    });
    return { inset: calls.some(([x,y,w,h]) => x === 12 && y === 12 && w === 80 && h === 80),
      labelsFit: points.every(([x,y]) => Math.abs(x) < .95 && Math.abs(y) < .95) };
  });
  check(marker.inset && marker.labelsFit, "XYZ marker and complete labels fit inside padded corner", JSON.stringify(marker));
  console.log("     timings", JSON.stringify(diag.ms));
});

await scenario("strict-csp", { strictCsp: true }, async () => {});

function assemblyFixture() {
  const scene = JSON.parse(gunzip(fixture.blob));
  const root = scene.shapes ?? scene;
  const other = structuredClone(root.parts[0]);
  other.id = "/Group/second"; other.name = "second";
  other.loc[0][0] += 75;
  root.parts.push(other);
  root.bb.xmax += 75;
  const blob = gzipSync(JSON.stringify(scene));
  return { blob, manifest: { ...fixture.manifest, scene_id: createHash("sha256").update(blob).digest("hex"), total_bytes: blob.length,
    triangles: fixture.manifest.triangles * 2,
    parts: [...fixture.manifest.parts, { name: "second", path: other.id }] } };
}

const assembly = assemblyFixture();
await scenario("part-reference-unavailable", { scene: assembly, strictCsp: true }, async ({ host, mcp }) => {
  await checkUnavailableReference({ host, mcp, check });
});
await scenario("part-reference-composer", { scene: assembly, strictCsp: true, capabilities: COMPOSER_CAPABILITIES }, async ({ host, mcp }) => {
  await checkComposerReference({ host, mcp, check });
});
await scenario("part-reference-rejected", { scene: assembly, strictCsp: true, capabilities: COMPOSER_CAPABILITIES }, async ({ host, mcp }) => {
  await checkRejectedComposerReference({ host, mcp, check });
});
await scenario("part-reference-extension-without-text", { scene: assembly, capabilities: { experimental: { "openai/message": {} }, message: {} } }, async ({ host, mcp }) => {
  await checkUnsupportedExtensionShape({ host, mcp, check });
});
for (const width of [320, 728]) await scenario(`initial-exploded-framing-${width}`, { scene: assembly, width,
  viewer: { presentation: { explode: true }, views: [
    { id: "plain", label: "Plain" },
    { id: "apart", label: "Apart", presentation: { explode: true } },
    { id: "side-plain", label: "Side plain", view: "right", presentation: { projection: "orthographic" } },
    { id: "side-apart", label: "Side apart", view: "right", presentation: { projection: "orthographic", explode: true } },
  ] } }, async ({ host }) => {
  const frame = host.frame(), button = name => frame.getByRole("button", { name, exact: true });
  const settle = () => frame.waitForTimeout(800);
  const choose = async label => { await button("Views").click(); await frame.getByRole("menuitemradio", { name: label, exact: true }).click(); };
  await checkFraming(frame, check, "base presentation opens exploded");
  await button("Put together").click();
  await settle();
  await checkFraming(frame, check, "base presentation collapses to fitted geometry");
  for (const [plain, apart] of [["Plain", "Apart"], ["Side plain", "Side apart"]]) {
    await choose(plain);
    const fittedZoom = await frame.evaluate(() => globalThis.__viewer.getCameraZoom());
    await choose(apart);
    await settle();
    await checkFraming(frame, check, `${apart} opens fitted`);
    await button("Put together").click();
    await settle();
    await checkFraming(frame, check, `${apart} collapses fitted`);
    check(await frame.evaluate(zoom => Math.abs(globalThis.__viewer.getCameraZoom() / zoom - 1) < 1e-7, fittedZoom), "initially exploded recipe restores the normal assembled fit");
  }
});
for (const width of [320, 728]) await scenario(`viewport-framing-${width}`, { scene: assembly, width,
  viewer: { views: [{ id: "side", label: "Side", view: "right", presentation: { projection: "orthographic" } }] } }, async ({ host }) => {
  const frame = host.frame(), button = name => frame.getByRole("button", { name, exact: true });
  const settle = () => frame.waitForTimeout(800);
  await checkFraming(frame, check, "initial model");
  await button("Pull apart").click();
  await settle();
  await checkFraming(frame, check, "exploded model");
  await host.page.setViewportSize({ width: width === 320 ? 900 : 300, height: 800 });
  await host.page.evaluate(width => document.getElementById("f").style.width = `${width}px`, width === 320 ? 880 : 280);
  await frame.waitForFunction(width => globalThis.__viewer.rendered.controls.domElement.clientWidth === width - 6, width === 320 ? 880 : 280);
  await checkFraming(frame, check, "resized exploded model");
  await button("Put together").click();
  await settle();
  await checkFraming(frame, check, "collapsed model after resize");
  await button("Views").click(); await frame.getByRole("menuitemradio", { name: "Side", exact: true }).click();
  await checkFraming(frame, check, "orthographic named view");
  await button("Reset").click();
  await checkFraming(frame, check, "reset named view");
});
await scenario("trackpad-navigation", { scene: assembly }, async ({ host }) => {
  await checkNavigation({ page: host.page, frame: host.frame(), check });
});
await scenario("concentric-exploded-layout", { scene: concentricFixture(fixture), strictCsp: true }, async ({ host }) => {
  await checkExplode({ frame: host.frame(), check });
});
await scenario("asymmetric-exploded-camera", { scene: concentricFixture(fixture, 4, true) }, async ({ host }) => {
  await checkExplodedCamera({ frame: host.frame(), check });
});
await scenario("large-exploded-camera", { scene: concentricFixture(fixture, 40) }, async ({ host }) => {
  await checkExplodedCamera({ frame: host.frame(), check, large: true });
});
await scenario("simple-controls-and-agent-views", { scene: assembly, strictCsp: true }, async ({ host, mcp, diag }) => {
  const frame = host.frame();
  const button = name => frame.getByRole("button", { name, exact: true });
  const camera = () => frame.evaluate(() => {
    const v = globalThis.__viewer;
    return [v.getCameraZoom(), ...v.getCameraPosition(), ...v.getCameraTarget(), ...v.getCameraQuaternion()];
  });
  const sameCamera = (before, after) => before.every((n, i) => Math.abs(n - after[i]) < 1e-8);
  const settle = () => frame.waitForTimeout(800);
  const update = async (viewer) => {
    await host.page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), {
      ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, viewer: { mode: "inline", ...viewer } },
    });
  };
  check(diag.triangles === assembly.manifest.triangles && diag.verified, "all original assembly triangles verified");
  check(await frame.getByRole("button").count() === 5 && await frame.getByRole("combobox").count() === 0, "only simple actions, Views, and a bigger-view button are visible");
  check(await frame.locator('.aad-cad input:visible, .aad-cad select:visible').count() === 0, "full engine capabilities expose no native inputs in chat");
  await frame.evaluate(() => globalThis.__viewer.setCameraZoom(globalThis.__viewer.getCameraZoom() * 1.25));
  const beforeExplode = await camera();
  await button("Pull apart").click();
  await settle();
  check(await frame.evaluate(() => globalThis.__viewer.state.get("animationMode") === "explode" && globalThis.__viewer.getRelativeTime() > 0.99), "pull apart completes at the separated endpoint");
  const explodedCamera = await camera();
  check(explodedCamera[0] < beforeExplode[0], "pull apart zooms out to fit the separated pieces");
  await frame.evaluate(() => globalThis.__viewer.setCameraZoom(globalThis.__viewer.getCameraZoom() * 0.8));
  await button("Put together").click();
  await settle();
  check(await frame.evaluate(() => globalThis.__viewer.state.get("animationMode") !== "explode"), "put together restores assembly");
  check(sameCamera(beforeExplode, await camera()), "put together restores the assembled zoom and framing");
  await button("Show grid").click();
  check(await frame.evaluate(() => globalThis.__viewer.getGrids().every(Boolean) && globalThis.__viewer.getAxes()), "one button shows all XYZ grids and axes");
  await button("Hide grid").click();
  const recipes = [
    { id: "interior", label: "Interior", description: "Cover hidden", presentation: { hide: ["/Group/main"] } },
    { id: "section", label: "Section", view: "front", presentation: { section: { planes: [{ plane: "XY", offset: 2 }] } } },
    { id: "stress", label: "Stress", analysis: { quantity: "Fixture maximum stress", units: "MPa", source: "Synthetic browser-test fixture", range: [10, 30],
      values: [{ part: "/Group/main", value: 10 }, { part: "/Group/second", value: 30 }] } },
  ];
  const choose = async label => { await button("Views").click(); await frame.getByRole("menuitemradio", { name: label, exact: true }).click(); };
  const colors = () => frame.evaluate(() => Object.fromEntries(Object.keys(globalThis.__viewer.getStates()).map(id => {
    const colors = []; globalThis.__viewer.rendered.nestedGroup.groups[id].traverse(m => { if(m.isMesh && m.material?.color) colors.push(m.material.color.getHexString()); });
    return [id, colors];
  })));
  const originalColors = await colors();
  await update({ views: recipes });
  await choose("Interior");
  check(await frame.evaluate(() => globalThis.__viewer.getState("/Group/main").every(v => v === 0) && globalThis.__viewer.getState("/Group/second")[0] === 1), "Interior preset reveals the original internal component");
  await button("Reset").click();
  check(await frame.evaluate(() => globalThis.__viewer.getState("/Group/main").every(v => v === 0)), "reset preserves the selected Interior recipe");
  await choose("Section");
  check(await frame.evaluate(() => globalThis.__viewer.getClipSlider(0) === 2 && globalThis.__viewer.state.get("activeTab") === "clip"), "Section preset applies its plane and camera");
  await choose("Stress");
  check(JSON.stringify(originalColors) !== JSON.stringify(await colors()), "supplied analysis values color the original component materials");
  check(await frame.locator(".aad-analysis").isVisible() && (await frame.locator(".aad-analysis").innerText()).includes("Synthetic browser-test fixture"), "analysis view shows quantity, units, range and actual source");
  await choose("Original");
  check(JSON.stringify(originalColors) === JSON.stringify(await colors()), "Original restores component materials after analysis");
  check(await frame.evaluate(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 1) && globalThis.__viewer.state.get("activeTab") === "tree"), "Original restores all parts and clears clipping");
  check(await frame.locator(".aad-analysis").isHidden(), "Original clears the analysis legend");
  await button("Views").press("Enter");
  await frame.getByRole("menuitemradio", { name: "Original", exact: true }).press("ArrowDown");
  await frame.getByRole("menuitemradio", { name: "Interior", exact: true }).press("Enter");
  check((await button("Views").getAttribute("title")).includes("Interior"), "view recipes support keyboard navigation and selection");
  await button("Views").click(); await frame.locator(".aad-view-menu").press("Escape");
  await frame.locator(".aad-view-menu").waitFor({ state: "hidden" });
  check(await frame.locator(".aad-view-menu").isHidden(), "Escape dismisses Views without exposing CAD controls");
  await button("Views").click(); await button("Views").click(); await button("Views").click();
  await frame.getByRole("menuitemradio", { name: "Original", exact: true }).click();
  check((await button("Views").getAttribute("title")).includes("Original"), "rapid close and reopen keeps Views usable");
  await button("Views").press("Enter"); await frame.locator(".aad-view-menu").press("Tab");
  check(await button("Pull apart").evaluate(e => e === document.activeElement), "Tab leaves the dropdown for the next control");
  await update({ presentation: { isolate: ["/Group/second"] }, title: "One piece" });
  await frame.waitForFunction(() => globalThis.__viewer.getState("/Group/main").every(v => v === 0));
  check(await frame.evaluate(() => globalThis.__viewer.getState("/Group/second")[0] === 1), "agent isolates a requested piece on the existing mesh");
  check(await button("Pull apart").isDisabled(), "single visible piece has no confusing explode action");
  await button("Reset").click();
  check(await frame.evaluate(() => globalThis.__viewer.getState("/Group/main").every(v => v === 0)), "reset keeps the agent's requested view");
  await update({ presentation: { hide: ["/Group/second"], style: "wireframe" } });
  await frame.waitForFunction(() => globalThis.__viewer.getState("/Group/main")[0] === 0 && globalThis.__viewer.getState("/Group/main")[1] === 1);
  check(await frame.evaluate(() => globalThis.__viewer.getState("/Group/second").every(v => v === 0)), "agent chooses display style and hidden parts without user panels");
  await update({ presentation: { section: { planes: [{ plane: "YZ", offset: -1 }, { normal: [0, -1, 0], offset: 0 }] }, projection: "orthographic", style: "transparent" }, view: "front" });
  await frame.waitForFunction(() => globalThis.__viewer.getClipSlider(0) === -1);
  check(await frame.evaluate(() => globalThis.__viewer.state.get("activeTab") === "clip" && globalThis.__viewer.getOrtho() && globalThis.__viewer.getTransparent()), "agent selects sections, projection, transparency and camera");
  check(await frame.getByRole("button").count() === 5 && await frame.getByRole("combobox").count() === 0, "advanced view settings never expose CAD controls");
  check(await frame.locator('.aad-cad input:visible, .aad-cad select:visible').count() === 0, "sections and analysis do not reveal native sliders or panels");
  await update({ presentation: {} });
  await frame.waitForFunction(() => globalThis.__viewer.state.get("activeTab") === "tree");
  check(await frame.evaluate(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 1)), "agent can restore all pieces");
  check(mcp.stats.chunks === 1 && await frame.evaluate(() => globalThis.__aad.loads === 1), "agent view changes do not reload or simplify the original scene");
  check(await button("Views").isDisabled(), "replacement result clears obsolete recipes");
  await frame.locator("body").press("t");
  check(await frame.evaluate(() => !globalThis.__viewer.getTransparent()), "hidden CAD shortcuts cannot surprise the user");
  await button("Make bigger").click();
  check((await host.hostState()).messages.some(m => m.method === "ui/request-display-mode"), "bigger view uses host gesture API");
  await button("Make smaller").click();
});

for (const width of [320, 390]) await scenario(`mobile-layout-${width}`, { width, height: 700, scene: assembly,
  viewer: { title: "Gearbox", views: [{ id: "interior", label: "Interior", presentation: { hide: ["/Group/main"] } }] } }, async ({ host }) => {
  const layout = await host.frame().evaluate(() => {
    const frame = document.querySelector(".aad-frame").getBoundingClientRect();
    const footer = document.querySelector(".aad-hint").getBoundingClientRect();
    const canvas = document.querySelector(".tcv_cad_viewer").getBoundingClientRect();
    const stage = document.querySelector(".aad-stage").getBoundingClientRect();
    const caption = document.querySelector('.aad-caption').getBoundingClientRect();
    const buttons = [...document.querySelectorAll('.aad-toolbar button')].filter(b => !b.hidden);
    const iconsFit = buttons.length === 4 && buttons[0].dataset.control === 'views' && buttons.every(button => {
      const icon = button.querySelector('svg').getBoundingClientRect(), bounds = button.getBoundingClientRect();
      return (button.dataset.control === 'views' || !button.textContent.trim()) && !!button.getAttribute('aria-label') && !!button.title && bounds.left >= caption.right
        && Math.abs((bounds.top + bounds.bottom) / 2 - (caption.top + caption.bottom) / 2) < 1
        && bounds.width === (button.dataset.control === 'views' ? 72 : 28) && bounds.height === 28 && bounds.right <= stage.right - 40
        && icon.left >= bounds.left && icon.right <= bounds.right && icon.bottom <= bounds.bottom;
    });
    return { height: document.documentElement.getBoundingClientRect().height, frame: frame.bottom, footer: footer.bottom, iconsFit,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, canvasRight: canvas.right, stageRight: stage.right };
  });
  check(layout.height >= layout.footer && layout.frame >= layout.footer, "mobile frame includes the complete bottom edge", JSON.stringify(layout));
  check(layout.overflow <= 1 && layout.canvasRight <= layout.stageRight + 1, "mobile CAD viewport has no horizontal clipping");
  check(layout.iconsFit, "four small icon buttons fit beside the title at phone width");
  await host.frame().getByRole("button", { name: "Views", exact: true }).click();
  const menuFits = await host.frame().locator('.aad-view-menu').evaluate(e => { const r=e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; });
  check(menuFits, "Views menu fits inside the phone viewport");
  await host.frame().getByRole("menuitemradio", { name: "Original", exact: true }).click();
  await host.frame().getByRole("button", { name: "Pull apart", exact: true }).click();
  check(await host.frame().getByRole("button", { name: "Put together", exact: true }).count() === 1, "phone control responds to a real tap target click");
});

const gridState = (target) => target.evaluate(() => {
  const v = globalThis.__viewer, button = document.querySelector('[data-control="grid"]');
  return { state: [...v.getGrids()], drawn: [...v.rendered.gridHelper.grid], axes: v.getAxes(), axesDrawn: v.rendered.axesHelper.visible, pressed: button.getAttribute("aria-pressed"), label: button.getAttribute("aria-label") };
});
const checkGrid = async (target, grids, axes, label) => {
  const got = await gridState(target);
  const pressed = grids.some(Boolean);
  const ok = JSON.stringify(got.state) === JSON.stringify(grids) && JSON.stringify(got.drawn) === JSON.stringify(grids)
    && got.axes === axes && got.axesDrawn === axes && got.pressed === String(pressed) && got.label === (pressed ? "Hide grid" : "Show grid");
  check(ok, label, ok ? "" : JSON.stringify(got));
};
const chooseView = async (target, label) => {
  await target.getByRole("button", { name: "Views", exact: true }).click();
  await target.getByRole("menuitemradio", { name: label, exact: true }).click();
};
const gridRecipes = [
  { id: "plain", label: "Plain", presentation: { hide: ["/Group/main"] } },
  { id: "grid-on", label: "Grid on", presentation: { grid: true } },
  { id: "grid-off", label: "Grid off", presentation: { grid: false } },
  { id: "section", label: "Section", view: "front", presentation: { section: { planes: [{ plane: "XY", offset: 2 }] } } },
];
const gridLabels = ["Plain", "Grid on", "Grid off", "Section", "Original"];
const postViewer = (host, mcp, viewer, scene = mcp.toolResult.structuredContent.scene) => host.page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), {
  ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, scene, viewer: { mode: "inline", ...viewer } },
});

await scenario("grid-persists-across-views", { scene: assembly, viewer: { views: gridRecipes } }, async ({ host, mcp }) => {
  const frame = host.frame();
  const button = name => frame.getByRole("button", { name, exact: true });
  const reset = () => frame.locator('[data-control="reset"]').click();
  const updateSameScene = async (title, viewer) => {
    await postViewer(host, mcp, { ...viewer, title });
    await frame.locator(".aad-caption", { hasText: title }).waitFor();
  };
  const walk = async (grids, axes, name) => {
    for (const label of gridLabels) {
      await chooseView(frame, label);
      await checkGrid(frame, grids, axes, `${name} survives ${label}`);
      await reset();
      await checkGrid(frame, grids, axes, `${name} survives Reset in ${label}`);
    }
  };
  await checkGrid(frame, [false, false, false], false, "a recipe without grid starts a new scene with it off");

  await button("Show grid").click();
  await checkGrid(frame, [true, true, true], true, "the user turns the grid on");
  await walk([true, true, true], true, "user grid on");
  await chooseView(frame, "Section");
  check(await frame.evaluate(() => globalThis.__viewer.getClipSlider(0) === 2), "Section still applies its own settings while the grid persists");
  await chooseView(frame, "Plain");
  check(await frame.evaluate(() => globalThis.__viewer.getState("/Group/main").every(v => v === 0)), "Plain still applies its own settings while the grid persists");
  await chooseView(frame, "Original");
  await button("Pull apart").click();
  await checkGrid(frame, [true, true, true], true, "user grid on survives Pull apart");
  await reset();
  await checkGrid(frame, [true, true, true], true, "user grid on survives Reset from a pulled-apart view");
  await updateSameScene("Base grid off", { presentation: { grid: false }, views: gridRecipes });
  await checkGrid(frame, [true, true, true], true, "same-scene explicit grid:false keeps the user's grid on");
  await updateSameScene("New recipes", { views: [...gridRecipes, { id: "added", label: "Added", presentation: { grid: false } }] });
  await chooseView(frame, "Added");
  await checkGrid(frame, [true, true, true], true, "an updated recipe list keeps the user's grid on");

  await button("Hide grid").click();
  await checkGrid(frame, [false, false, false], false, "the user turns the grid off");
  await walk([false, false, false], false, "user grid off");
  await updateSameScene("Base grid on", { presentation: { grid: true }, views: gridRecipes });
  await checkGrid(frame, [false, false, false], false, "same-scene explicit grid:true keeps the user's grid off");

  for (const [grids, axes] of [[[true, false, true], false], [[false, true, false], true], [[false, false, false], true]]) {
    await frame.evaluate(([g, a]) => { globalThis.__viewer.setGrids(g); globalThis.__viewer.setAxes(a); }, [grids, axes]);
    await walk(grids, axes, `planes ${grids.map(Number).join("")} axes ${Number(axes)}`);
    await updateSameScene(`Flags ${grids.map(Number).join("")} ${Number(axes)}`, { presentation: { grid: true }, views: gridRecipes });
    await checkGrid(frame, grids, axes, "same-scene update keeps individual planes and axes");
  }
  check(await frame.evaluate(() => globalThis.__aad.loads === 1), "grid preservation does not reload the original scene");

  await frame.evaluate(() => { globalThis.__viewer.setGrids([true, false, false]); globalThis.__viewer.setAxes(true); });
  await postViewer(host, mcp, { presentation: { hide: ["injected failure"] } });
  await frame.waitForFunction(() => globalThis.__aad.state === "error");
  await checkGrid(frame, [true, false, false], true, "a rejected same-scene request does not clear the user's grid");
});

function sceneRevision(revision) {
  const blob = gzipSync(JSON.stringify({ ...JSON.parse(gunzip(assembly.blob)), _revision: revision }));
  return { blob, manifest: { ...assembly.manifest, scene_id: createHash("sha256").update(blob).digest("hex"), total_bytes: blob.length } };
}
const revisions = [assembly, sceneRevision(2), sceneRevision(3)];
const readRevision = ({ scene_id, offset, length }) => {
  const { blob } = revisions.find(r => r.manifest.scene_id === scene_id);
  const part = blob.subarray(offset, offset + length);
  return { content: [{ type: "text", text: `${part.length} bytes` }], structuredContent: { data_b64: part.toString("base64"), offset, byte_count: part.length, total_bytes: blob.length, has_more: offset + part.length < blob.length } };
};
await scenario("grid-new-scene-uses-its-recipe", { scene: assembly, fakeOpts: { extra: { aad_scene_read: readRevision } }, viewer: { presentation: { grid: true }, views: gridRecipes } }, async ({ host, mcp }) => {
  const frame = host.frame();
  const button = name => frame.getByRole("button", { name, exact: true });
  const nextScene = async (index, viewer) => {
    await postViewer(host, mcp, viewer, revisions[index].manifest);
    await frame.waitForFunction(loads => globalThis.__aad.loads === loads && globalThis.__aad.state === "ready", index + 1);
  };
  await checkGrid(frame, [true, true, true], true, "initial recipe grid:true turns the grid on");
  await button("Hide grid").click();
  await frame.locator('[data-control="reset"]').click();
  await checkGrid(frame, [false, false, false], false, "Reset does not reapply the initial grid:true over the user's choice");
  await nextScene(1, { presentation: { grid: true } });
  await checkGrid(frame, [true, true, true], true, "a new scene uses its own grid:true over the previous preference");
  await nextScene(2, { presentation: {} });
  await checkGrid(frame, [false, false, false], false, "a new scene without grid starts off over the previous preference");
  check((await frame.evaluate(() => globalThis.__aad.triangles)) === assembly.manifest.triangles, "new scenes keep every original triangle");
});

{
  const asset = `data:image/svg+xml;base64,${Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg"><metadata id="aad-scene">${assembly.blob.toString("base64")}</metadata></svg>`).toString("base64")}`;
  const config = JSON.stringify({ manifest: assembly.manifest, asset, controls: "cad", views: gridRecipes }).replaceAll("<", "\\u003c");
  const file = resolve(OUT, "grid-cad.html");
  writeFileSync(file, html.replace("<head>", () => `<head><script>window.__AAD_EMBEDDED_SCENE__ = ${config};</script>`));
  const browser = await chromium.launch({ headless: true, channel: "chromium", args: gpu ? ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"] : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  try {
    const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 } })).newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(`file://${file}`);
    await page.waitForFunction(() => ["ready", "error"].includes(globalThis.__aad?.state), null, { timeout: 60_000 });
    check(await page.evaluate(() => globalThis.__aad.state) === "ready", "full CAD grid page reaches ready");
    check(await page.locator('.aad-stage').evaluate(e => e.getBoundingClientRect().height > 700), "standalone CAD canvas fills the browser window");
    check(await page.getByRole('button', { name: 'Make bigger', exact: true }).count() === 1, "window filling does not pretend to enter browser fullscreen");
    await page.setViewportSize({ width: 1200, height: 1100 });
    await page.waitForFunction(() => document.querySelector('.aad-stage').getBoundingClientRect().height > 1000);
    check(true, "standalone canvas expands with browser height");
    await page.setViewportSize({ width: 1200, height: 800 });
    await checkFraming(page, check, "native CAD model", 24);
    check(await page.locator(".tcv_cad_toolbar").isVisible() && await page.locator(".tcv_cad_navigation").isVisible(), "full CAD grid page retains the native toolbar and tree");
    const nativeChecks = () => page.evaluate(() => ["xy", "xz", "yz"].map(p => document.querySelector(`.tcv_grid-${p}`).checked));
    for (const plane of ["xy", "yz"]) await page.locator(`.tcv_grid-${plane}`).evaluate(e => e.click());
    await page.evaluate(() => globalThis.__viewer.setAxes(true));
    await checkGrid(page, [true, false, true], true, "native CAD plane controls set individual planes");
    for (const label of gridLabels) {
      await chooseView(page, label);
      await checkGrid(page, [true, false, true], true, `full CAD planes survive ${label}`);
      check(JSON.stringify(await nativeChecks()) === "[true,false,true]", `native plane checkboxes stay synced after ${label}`);
    }
    await page.locator('[data-control="reset"]').click();
    await checkGrid(page, [true, false, true], true, "full CAD planes survive the AAD Reset");
    await page.locator(".tcv_grid-xy").evaluate(e => e.click());
    await chooseView(page, "Grid on");
    await checkGrid(page, [false, false, true], true, "a native plane change remains the preference for later views");
    await page.locator('[data-path="/Group"] > .tv-node-content > .tv-nav-marker').click();
    const partIcon = page.locator('[data-path="/Group/main"] .tv-icon0');
    await partIcon.click();
    await page.evaluate(() => { const v = globalThis.__viewer; v.setExplode(true); v.setRelativeTime(1); });
    await partIcon.click();
    check(await page.evaluate(() => {
      const v = globalThis.__viewer;
      const boxes = Object.keys(v.getStates()).map(id => v.bbox.clone().makeEmpty().expandByObject(v.rendered.nestedGroup.groups[id], false));
      return Object.values(v.getStates()).every(s => s.includes(1)) && Math.max(boxes[1].min.x - boxes[0].max.x, boxes[0].min.x - boxes[1].max.x) > 20;
    }), "native tree re-showing a part regenerates the exploded layout");
    await page.evaluate(() => globalThis.__viewer.setExplode(false));
    check(errors.length === 0, "full CAD grid page has no page errors", errors.join(" | "));
    await page.screenshot({ path: `${OUT}/grid-cad.png` });
  } finally { await browser.close(); }
}

await scenario("theme-backgrounds", {}, async ({ host }) => {
  const background = () => host.frame().evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = getComputedStyle(document.querySelector('.aad-stage')).backgroundColor;
    ctx.fillRect(0, 0, 1, 1);
    return [...ctx.getImageData(0, 0, 1, 1).data];
  });
  const dark = await background();
  await host.page.evaluate(() => window.__setTheme('light'));
  await host.frame().waitForFunction(() => document.querySelector('.aad-frame').dataset.theme === 'light');
  const light = await background();
  check(dark[0] > 30 && dark[0] < 65 && dark[0] === dark[1] && dark[1] === dark[2], "dark viewport uses a slightly lighter neutral gray", JSON.stringify(dark));
  check(light[0] > 220 && light[0] < 250 && light[0] === light[1] && light[1] === light[2], "light viewport uses a slightly darker neutral gray", JSON.stringify(light));
});

const big = bigScene(5 * 1024 * 1024);
await scenario("big-scene-chunks", { scene: big }, async ({ host, mcp, diag }) => {
  const state = await host.hostState();
  const expected = Math.ceil(big.blob.length / (128 * 1024));
  check(mcp.stats.chunks === expected, "chunk count matches size", `${mcp.stats.chunks}/${expected}`);
  check(mcp.stats.maxChunk <= 128 * 1024, "chunks are at most 128 KiB", String(mcp.stats.maxChunk));
  check(mcp.stats.maxInFlight <= 4, "at most 4 calls in flight", String(mcp.stats.maxInFlight));
  check(state.tooLarge === 0, "no app message over 256 KiB");
  console.log(`     ${(big.blob.length / 1048576).toFixed(2)} MiB compressed, timings`, JSON.stringify(diag.ms));
});

await scenario("chunk-retry", { scene: big, fakeOpts: { failFirstReadAt: 128 * 1024 } }, async ({ mcp, diag }) => {
  check(mcp.stats.failed === 1, "the injected failure happened");
  check(diag.load.retries === 1, "the chunk was retried", String(diag.load.retries));
});

// Exercise the HTML-only bundled transport with >14 MiB of incompressible scene bytes.
{
  const source = JSON.parse(gunzipSync(assembly.blob));
  source._synthetic_padding = randomBytes(14 * 1024 * 1024).toString("base64");
  const payload = gzipSync(JSON.stringify(source), { level: 0 });
  const chunkSize = 1024 * 1024;
  const chunks = Array.from({ length: Math.ceil(payload.length / chunkSize) }, (_, i) => payload.subarray(i * chunkSize, (i + 1) * chunkSize));
  const assets = chunks.map(pngData);
  const manifest = { ...assembly.manifest, total_bytes: payload.length, scene_id: createHash("sha256").update(payload).digest("hex") };
  const config = JSON.stringify({ manifest, assets, chunk_bytes: chunks.map(x => x.length) }).replaceAll("<", "\\u003c");
  const file = resolve(OUT, "large-bundled-scene.html");
  writeFileSync(file, html.replace("<head>", `<head><script>window.__AAD_EMBEDDED_SCENE__=${config};</script>`));
  const browser = await chromium.launch({ headless: true, channel: "chromium", args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  try {
    const page = await (await browser.newContext()).newPage();
    const start = Date.now(); await page.goto(`file://${file}`);
    await page.waitForFunction(() => ["ready", "error"].includes(globalThis.__aad?.state), null, { timeout: 120000 });
    check(await page.evaluate(() => globalThis.__aad.state) === "ready", "14 MiB chunked bundled scene reaches ready");
    check(payload.length >= 14 * 1024 * 1024 && chunks.length > 1, "synthetic scene exceeds 14 MiB and spans chunks", `${(payload.length / 1048576).toFixed(2)} MiB / ${chunks.length} chunks`);
    console.log(`     bundled scene load ${(Date.now() - start)} ms`);
    const missingConfig = { manifest, assets: [...assets], chunk_bytes: chunks.map(x => x.length) };
    missingConfig.assets[1] = "";
    const missingFile = resolve(OUT, "large-bundled-scene-missing-chunk.html");
    writeFileSync(missingFile, html.replace("<head>", `<head><script>window.__AAD_EMBEDDED_SCENE__=${JSON.stringify(missingConfig)};</script>`));
    await page.goto(`file://${missingFile}`);
    await page.waitForFunction(() => globalThis.__aad?.state === "error", null, { timeout: 30000 });
    check(String(await page.evaluate(() => globalThis.__aad.error)).includes("chunk 1 is missing"), "a missing PNG chunk reports an explicit error");
  } finally { await browser.close(); }
}

if (failures) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("e2e ok");
