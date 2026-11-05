// The real path: real `aad mcp` over stdio, a real build, the packaged viewer, a real browser.
// The host is the reference host from ref-host.mjs. Needs `npm run build` and a synced workspace venv.
//   AAD_PYTHON=../.venv/bin/python node scripts/e2e-real.mjs
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { chromium } from "playwright";
import { openRefHost } from "./ref-host.mjs";

const OUT = "test-output";
mkdirSync(OUT, { recursive: true });
const PYTHON = process.env.AAD_PYTHON ?? "python";
const BRACKET = `
from build123d import *
with BuildPart() as bp:
    Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((0, -17, 6)):
        Box(60, 6, 34, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations(Plane.XY.offset(6)):
        with GridLocations(46, 22, 2, 2):
            with Locations((0, 3)):
                CounterBoreHole(radius=2.2, counter_bore_radius=4, counter_bore_depth=2.2, depth=6)
    with Locations(Plane(origin=(0, -14, 22), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):
        Hole(radius=13, depth=6)
    fillet(bp.edges().filter_by(Axis.Y).group_by(Axis.Z)[-1], radius=4)
result = bp.part
`;

let failures = 0;
const check = (ok, label, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${detail ? " " + detail : ""}`);
  if (!ok) failures++;
};

function adapter(home) {
  const env = { ...process.env, HOME: home, AAD_HOME: join(home, ".aad"), AAD_IDLE_TIMEOUT_S: "60" };
  const child = spawn(PYTHON, ["-m", "aad_platform.cli", "mcp"], { env, stdio: ["pipe", "pipe", "ignore"] });
  const pending = new Map();
  let nextId = 1;
  createInterface({ input: child.stdout }).on("line", (line) => {
    const message = JSON.parse(line);
    if (message.method && message.id !== undefined) {
      child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "unsupported" } }) + "\n");
    } else if (pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  });
  const request = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
      child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  return {
    request,
    tool: (name, args) => request("tools/call", { name, arguments: args }),
    notify: (method) => child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method }) + "\n"),
    close() {
      child.stdin.end();
      child.kill();
    },
  };
}

// A part drawn on the frame's background leaves many distinct colors; an empty canvas leaves few.
async function colorCount(page, frame) {
  return (frame ?? page).evaluate(() => {
    const source = [...document.querySelectorAll("canvas")].find((c) => c.width > 200 && c.height > 200);
    const viewer = globalThis.__viewer;
    viewer?.update?.(true, false);
    const copy = document.createElement("canvas");
    copy.width = 160;
    copy.height = 120;
    const ctx = copy.getContext("2d");
    ctx.drawImage(source, 0, 0, copy.width, copy.height);
    const { data } = ctx.getImageData(0, 0, copy.width, copy.height);
    const seen = new Set();
    for (let i = 0; i < data.length; i += 4) seen.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
    return seen.size;
  });
}

const home = mkdtempSync(join("/tmp", "aad-viewer-"));
const mcp = adapter(home);
try {
  await mcp.request("initialize", { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "aad-viewer-e2e", version: "1" } });
  mcp.notify("notifications/initialized");
  await mcp.tool("aad_new", { name: "bracket" });
  const built = await mcp.tool("aad_build", { project: "bracket", source: BRACKET, wait_s: 200 });
  check(built.structuredContent?.ok === true, "real build", built.structuredContent?.summary);
  const shown = await mcp.tool("aad_show", { project: "bracket" });
  const manifest = shown.structuredContent.scene;
  check(!!manifest?.scene_id, "aad_show returns a scene", String(manifest?.triangles) + " triangles");
  const resource = await mcp.request("resources/read", { uri: "ui://aad/viewer" });
  const html = resource.contents[0].text;
  check(resource.contents[0].mimeType === "text/html;profile=mcp-app", "ui://aad/viewer has the MCP Apps mime type");
  check(html.includes("three-cad-viewer") || html.length > 1_000_000, "ui://aad/viewer serves the real bundle", `${(html.length / 1048576).toFixed(2)} MiB`);
  let reads = 0;
  const presentation = { section: { planes: [{ plane: "YZ", offset: -1 }] }, style: "transparent", projection: "orthographic" };
  const onRequest = async (method, params) => {
    if (method !== "tools/call") return {};
    if (params.name === "aad_scene_read") reads++;
    return mcp.tool(params.name, params.arguments);
  };

  for (const strictCsp of [false, true]) {
    const name = strictCsp ? "real-part-inline-strict-csp" : "real-part-inline";
    const host = await openRefHost({ html, toolResult: shown, onRequest, strictCsp, gpu: process.env.AAD_E2E_GPU === "1" });
    try {
      const diag = await host.waitReady();
      check(diag.state === "ready", `${name}: reaches ready`, diag.error ?? "");
      check(reads > 0, `${name}: chunks came through aad_scene_read`, String(reads));
      const colors = await colorCount(host.page, host.frame());
      check(colors > 50, `${name}: the canvas shows a shaded part, not an empty frame`, `${colors} colors`);
      const before = reads;
      const prepared = await mcp.tool("aad_show", { project: "bracket", presentation, view: "front", title: "Look inside" });
      check(prepared.structuredContent?.scene?.scene_id === manifest.scene_id, `${name}: agent view keeps the original scene`);
      await host.page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), prepared);
      await host.frame().waitForFunction(() => globalThis.__viewer.getClipSlider(0) === -1 && globalThis.__viewer.getOrtho());
      check(reads === before && await host.frame().evaluate(() => globalThis.__aad.loads === 1 && globalThis.__viewer.getTransparent()), `${name}: real agent section updates without another mesh load`);
      await host.frame().getByRole("button", { name: "Reset", exact: true }).click();
      check(await host.frame().evaluate(() => globalThis.__viewer.getClipSlider(0) === -1), `${name}: reset keeps the requested section`);
      check(host.requests.length === 0, `${name}: no network requests`, host.requests.join(" "));
      const bad = host.logs.filter((l) => /\[(error|pageerror|requestfailed)\]/.test(l));
      check(bad.length === 0, `${name}: no console errors`, bad.join(" | "));
      console.log("     timings", JSON.stringify(diag.ms));
    } finally {
      await host.page.screenshot({ path: `${OUT}/${name}.png` }).catch(() => {});
      await host.close();
    }
  }

  // The standalone export opens from disk, with nothing running behind it.
  const output = join(home, "viewer-export");
  const sceneDir = join(home, "AAD", "bracket", ".aad", "revs", String(manifest.rev).padStart(6, "0"), "scene");
  const exported = spawnSync(PYTHON, ["-m", "aad_platform.viewer.bundle", sceneDir, output, "--presentation", JSON.stringify(presentation), "--view", "front", "--title", "Look inside"], { encoding: "utf8" });
  const file = join(output, "standalone.html");
  check(exported.status === 0, "bundle export writes the standalone viewer", exported.stderr);
  mcp.close();
  const browser = await chromium.launch({ headless: true, channel: "chromium", args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const page = await (await browser.newContext({ viewport: { width: 900, height: 620 } })).newPage();
  const offline = [];
  page.on("request", (r) => { if (!r.url().startsWith("file:")) offline.push(r.url().slice(0, 120)); });
  await page.goto(`file://${file}`);
  await page.waitForFunction(() => ["ready", "error"].includes(globalThis.__aad?.state), null, { timeout: 60_000 });
  const state = await page.evaluate(() => globalThis.__aad);
  check(state.state === "ready", "standalone page reaches ready", state.error ?? "");
  check(await page.evaluate(() => globalThis.__viewer.getClipSlider(0) === -1 && globalThis.__viewer.getOrtho() && globalThis.__viewer.getTransparent()), "standalone page keeps the prepared inspection view");
  const colors = await colorCount(page);
  check(colors > 20, "standalone page shows the prepared translucent section", `${colors} colors`);
  check(offline.length === 0, "standalone page makes no requests", offline.join(" "));
  await page.screenshot({ path: `${OUT}/real-part-standalone.png` });
  await browser.close();
} finally {
  mcp.close();
  try {
    const info = JSON.parse(readFileSync(join(home, ".aad", "run", "aadd.json"), "utf8"));
    process.kill(info.pid, "SIGTERM");
  } catch {}
  rmSync(home, { recursive: true, force: true });
}
if (failures) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("real e2e ok");
