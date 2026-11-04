import { defineConfig, type Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

/**
 * three-cad-viewer lists Poly Haven HDR environment maps that it fetches at runtime.
 * The viewer must not use the network (CONTRACTS section 8), so the remote list is removed.
 * The procedural "studio" environment still works.
 */
function removeRemoteEnvironmentMaps(): Plugin {
  return {
    name: "aiad:remove-remote-environment-maps",
    enforce: "pre",
    transform(code, id) {
      if (!id.includes("three-cad-viewer")) return null;
      const next = code
        .replace(/const PH_BASE = "[^"]*";/, 'const PH_BASE = "";')
        .replace(/const DEFAULT_PRESET_SLUGS = \[[\s\S]*?\];/, "const DEFAULT_PRESET_SLUGS = [];");
      return next === code ? null : { code: next, map: null };
    },
  };
}

export default defineConfig({
  plugins: [removeRemoteEnvironmentMaps(), viteSingleFile({ removeViteModuleLoader: true })],
  build: {
    outDir: "dist",
    target: "es2022",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    chunkSizeWarningLimit: 4000,
  },
});
