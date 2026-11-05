// End-to-end viewer checks in a real browser through the reference host. Needs `npm run build` first.
// Set AAD_E2E_GPU=1 for real-GPU numbers on a Mac; the default software GL also runs in CI.
import { mkdirSync, readFileSync } from "node:fs";
import { gunzipSync, gzipSync } from "node:zlib";
import { createHash } from "node:crypto";
import { openRefHost } from "./ref-host.mjs";
import { fakeMcp, loadFixture } from "./fake-mcp.mjs";

const OUT = "test-output";
mkdirSync(OUT, { recursive: true });
const html = readFileSync("dist/aad-viewer.html", "utf8");
const gpu = process.env.AAD_E2E_GPU === "1";
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
  check(await host.frame().getByRole("button", { name: "Show inside", exact: true }).count() === 0, "part without an interior view has no inside button");
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
await scenario("simple-controls-and-agent-views", { scene: assembly, strictCsp: true }, async ({ host, mcp, diag }) => {
  const frame = host.frame();
  const button = name => frame.getByRole("button", { name, exact: true });
  const camera = () => frame.evaluate(() => {
    const v = globalThis.__viewer;
    return [v.getCameraZoom(), ...v.getCameraPosition(), ...v.getCameraTarget(), ...v.getCameraQuaternion()];
  });
  const sameCamera = (before, after) => before.every((n, i) => Math.abs(n - after[i]) < 1e-8);
  const update = async (viewer) => {
    await host.page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), {
      ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, viewer: { mode: "inline", ...viewer } },
    });
  };
  check(diag.triangles === assembly.manifest.triangles && diag.verified, "all original assembly triangles verified");
  check(await frame.getByRole("button").count() === 4 && await frame.getByRole("combobox").count() === 0, "only three simple actions and a bigger-view button are visible");
  await frame.evaluate(() => globalThis.__viewer.setCameraZoom(globalThis.__viewer.getCameraZoom() * 1.25));
  const beforeExplode = await camera();
  await button("Pull apart").click();
  check(await frame.evaluate(() => globalThis.__viewer.state.get("animationMode") === "explode" && globalThis.__viewer.getRelativeTime() > 0.99), "pull apart separates the pieces immediately");
  const explodedCamera = await camera();
  check(explodedCamera[0] < beforeExplode[0], "pull apart zooms out to fit the separated pieces");
  await frame.evaluate(() => globalThis.__viewer.setCameraZoom(globalThis.__viewer.getCameraZoom() * 0.8));
  await button("Put together").click();
  check(await frame.evaluate(() => globalThis.__viewer.state.get("animationMode") !== "explode"), "put together restores assembly");
  check(sameCamera(beforeExplode, await camera()), "put together restores the assembled zoom and framing");
  await button("Show grid").click();
  check(await frame.evaluate(() => globalThis.__viewer.getGrids().every(Boolean) && globalThis.__viewer.getAxes()), "one button shows all XYZ grids and axes");
  await button("Hide grid").click();
  await update({ presentation: { inside: { hide: ["/Group/main"] } } });
  await button("Show inside").waitFor();
  const outsideCamera = await camera();
  await button("Show inside").click();
  check(await frame.evaluate(() => globalThis.__viewer.getState("/Group/main").every(v => v === 0) && globalThis.__viewer.getState("/Group/second")[0] === 1), "inside toggle hides the cover and keeps the interior mesh");
  await button("Show outside").click();
  check(await frame.evaluate(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 1)), "outside toggle restores the cover");
  check(sameCamera(outsideCamera, await camera()), "outside toggle restores the complete model's camera");
  await button("Show inside").click();
  await button("Reset").click();
  check(await button("Show inside").getAttribute("aria-pressed") === "false" && await frame.evaluate(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 1)), "reset returns from inside to the prepared outside view");
  await update({ presentation: { isolate: ["/Group/second"], inside: { hide: ["/Group/main"] } } });
  await frame.waitForFunction(() => globalThis.__viewer.getState("/Group/main").every(v => v === 0));
  check(await button("Show inside").count() === 0, "inside control disappears when the current selection has no visible cover");
  await update({ presentation: { inside: { section: { planes: [{ plane: "XY", offset: 2 }] } } } });
  await button("Show inside").click();
  check(await frame.evaluate(() => globalThis.__viewer.getClipSlider(0) === 2 && globalThis.__viewer.state.get("activeTab") === "clip"), "declared cavity view can use a section instead of hiding a cover");
  await button("Show outside").click();
  check(await frame.evaluate(() => globalThis.__viewer.state.get("activeTab") === "tree"), "outside toggle clears the temporary cavity section");
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
  check(await frame.getByRole("button").count() === 4 && await frame.getByRole("combobox").count() === 0, "advanced view settings never expose CAD controls");
  await update({ presentation: {} });
  await frame.waitForFunction(() => globalThis.__viewer.state.get("activeTab") === "tree");
  check(await frame.evaluate(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 1)), "agent can restore all pieces");
  check(mcp.stats.chunks === 1 && await frame.evaluate(() => globalThis.__aad.loads === 1), "agent view changes do not reload or simplify the original scene");
  await frame.locator("body").press("t");
  check(await frame.evaluate(() => !globalThis.__viewer.getTransparent()), "hidden CAD shortcuts cannot surprise the user");
  await button("Make bigger").click();
  check((await host.hostState()).messages.some(m => m.method === "ui/request-display-mode"), "bigger view uses host gesture API");
  await button("Make smaller").click();
});

for (const width of [320, 390]) await scenario(`mobile-layout-${width}`, { width, height: 700, scene: assembly,
  viewer: { title: "Gearbox", presentation: { inside: { hide: ["/Group/main"] } } } }, async ({ host }) => {
  const layout = await host.frame().evaluate(() => {
    const frame = document.querySelector(".aad-frame").getBoundingClientRect();
    const footer = document.querySelector(".aad-hint").getBoundingClientRect();
    const canvas = document.querySelector(".tcv_cad_viewer").getBoundingClientRect();
    const stage = document.querySelector(".aad-stage").getBoundingClientRect();
    const caption = document.querySelector('.aad-caption').getBoundingClientRect();
    const buttons = [...document.querySelectorAll('.aad-toolbar button')].filter(b => !b.hidden);
    const iconsFit = buttons.length === 4 && buttons.every(button => {
      const icon = button.querySelector('svg').getBoundingClientRect(), bounds = button.getBoundingClientRect();
      return !button.textContent.trim() && !!button.getAttribute('aria-label') && !!button.title && bounds.left >= caption.right
        && Math.abs((bounds.top + bounds.bottom) / 2 - (caption.top + caption.bottom) / 2) < 1
        && bounds.width === 32 && bounds.height === 32 && bounds.right <= stage.right - 44
        && icon.left >= bounds.left && icon.right <= bounds.right && icon.bottom <= bounds.bottom;
    });
    return { height: document.documentElement.getBoundingClientRect().height, frame: frame.bottom, footer: footer.bottom, iconsFit,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, canvasRight: canvas.right, stageRight: stage.right };
  });
  check(layout.height >= layout.footer && layout.frame >= layout.footer, "mobile frame includes the complete bottom edge", JSON.stringify(layout));
  check(layout.overflow <= 1 && layout.canvasRight <= layout.stageRight + 1, "mobile CAD viewport has no horizontal clipping");
  check(layout.iconsFit, "four small icon buttons fit beside the title at phone width");
  await host.frame().getByRole("button", { name: "Pull apart", exact: true }).click();
  check(await host.frame().getByRole("button", { name: "Put together", exact: true }).count() === 1, "phone control responds to a real tap target click");
});

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

if (failures) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("e2e ok");
