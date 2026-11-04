// Fails the build if the single-file viewer can reach APIs that CONTRACTS section 8 forbids.
// Allowed `fetch(` sites are three.js loaders that only run when given a URL; we never give them one.
import { readFileSync } from "node:fs";

const html = readFileSync("dist/aiad-viewer.html", "utf8");
const forbidden = [
  "new Function(", "eval(", "new Worker(", "SharedWorker(", "WebAssembly.", "importScripts(",
  "localStorage", "sessionStorage", "indexedDB", "document.cookie", "XMLHttpRequest(",
  "new WebSocket(", "navigator.sendBeacon", "polyhaven.org",
];
const allowedCounts = { "new WebSocket(": 1 }; // window transport only; never constructed in an MCP App
const expectedFetchSites = 3;
let failed = false;
for (const needle of forbidden) {
  const count = html.split(needle).length - 1;
  const allowed = allowedCounts[needle] ?? 0;
  if (count > allowed) {
    failed = true;
    console.error(`FORBIDDEN ${needle}: ${count} (allowed ${allowed})`);
  }
}
const fetches = html.split("fetch(").length - 1;
if (fetches > expectedFetchSites) {
  failed = true;
  console.error(`fetch( sites grew to ${fetches}, expected at most ${expectedFetchSites}. Audit the new one.`);
}
if (!html.includes('<script type="module">') || /<script[^>]+src=/.test(html) || /<link[^>]+href=/.test(html)) {
  failed = true;
  console.error("Viewer must be one inline file with no external script or stylesheet.");
}
if (failed) process.exit(1);
console.log(`audit ok: ${(html.length / 1048576).toFixed(3)} MiB, fetch sites ${fetches}`);
