// Renames Vite's single-file output to the name in CONTRACTS section 1 and enforces the size limit.
import { renameSync, statSync } from "node:fs";

const MAX_BYTES = 3 * 1024 * 1024;
renameSync("dist/index.html", "dist/aiad-viewer.html");
const size = statSync("dist/aiad-viewer.html").size;
console.log(`dist/aiad-viewer.html ${(size / 1048576).toFixed(3)} MiB`);
if (size > MAX_BYTES) {
  console.error(`Viewer is ${size} bytes, over the ${MAX_BYTES} byte limit.`);
  process.exit(1);
}
