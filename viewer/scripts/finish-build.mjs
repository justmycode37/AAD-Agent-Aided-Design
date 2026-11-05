// Renames Vite's single-file output to the name in CONTRACTS section 1 and enforces the size limit.
import { copyFileSync, readFileSync, readdirSync, renameSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const MAX_BYTES = 3 * 1024 * 1024;
renameSync("dist/index.html", "dist/aad-viewer.html");
const size = statSync("dist/aad-viewer.html").size;
console.log(`dist/aad-viewer.html ${(size / 1048576).toFixed(3)} MiB`);
if (size > MAX_BYTES) {
  console.error(`Viewer is ${size} bytes, over the ${MAX_BYTES} byte limit.`);
  process.exit(1);
}
// The wheel ships this copy, so that installing AAD does not need Node.
copyFileSync("dist/aad-viewer.html", "../packages/platform/src/aad_platform/viewer/aad-viewer.html");

// Include dependency notices in the Python distribution alongside the runtime.
const lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
const notices = [];
for (const [path, entry] of Object.entries(lock.packages).sort(([a], [b]) => a.localeCompare(b))) {
  if (!path || entry.dev) continue;
  const pkg = JSON.parse(readFileSync(join(path, "package.json"), "utf8"));
  for (const name of readdirSync(path).sort()) {
    if (/^(licen[cs]e|notice|copying)(\.|$)/i.test(name) && statSync(join(path, name)).isFile()) {
      notices.push(`${pkg.name}@${pkg.version} — ${name}\n${readFileSync(join(path, name), "utf8")}`);
    }
  }
}
writeFileSync("../packages/platform/src/aad_platform/viewer/THIRD_PARTY_LICENSES.txt", notices.join("\n\n====================\n\n"));
