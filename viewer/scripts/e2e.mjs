// End-to-end viewer checks in a real browser through the reference host. Needs `npm run build` first.
// Set AIAD_E2E_GPU=1 for real-GPU numbers on a Mac; the default software GL also runs in CI.
import { mkdirSync, readFileSync } from "node:fs";
import { gunzipSync, gzipSync } from "node:zlib";
import { createHash } from "node:crypto";
import { openRefHost } from "./ref-host.mjs";
import { fakeMcp, loadFixture } from "./fake-mcp.mjs";

const OUT = "test-output";
mkdirSync(OUT, { recursive: true });
const html = readFileSync("dist/aiad-viewer.html", "utf8");
const gpu = process.env.AIAD_E2E_GPU === "1";
const fixture = loadFixture("tests/fixtures/bracket");
let failures = 0;
const check = (ok, label, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${detail ? " " + detail : ""}`);
  if (!ok) failures++;
};

async function scenario(name, opts, fn) {
  const { fakeOpts, scene = fixture, ...hostOpts } = opts;
  const mcp = fakeMcp(scene, fakeOpts);
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
  check(state.sized === 560, "requests its inline height", String(state.sized));
  check(state.tooLarge === 0, "no app message over 256 KiB");
  check(mcp.stats.chunks === 1, "one chunk for a small scene", String(mcp.stats.chunks));
  console.log("     timings", JSON.stringify(diag.ms));
});

await scenario("strict-csp", { strictCsp: true }, async () => {});

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
