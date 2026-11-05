// Fails if the viewer the wheel ships is not what this source builds. Run `npm run build` to refresh it.
import { readFileSync } from "node:fs";

const built = readFileSync("dist/aad-viewer.html");
const packaged = readFileSync("../packages/platform/src/aad_platform/viewer/aad-viewer.html");
if (!built.equals(packaged)) {
  console.error("packages/platform/.../viewer/aad-viewer.html is stale. Run `npm run build` in viewer/ and commit it.");
  process.exit(1);
}
console.log("packaged viewer matches the build");
