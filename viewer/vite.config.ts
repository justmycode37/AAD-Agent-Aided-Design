import { defineConfig, type Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

/**
 * three-cad-viewer lists Poly Haven HDR environment maps that it fetches at runtime.
 * The viewer must not use the network (CONTRACTS section 8), so the remote list is removed.
 * The procedural "studio" environment still works.
 */
function removeRemoteEnvironmentMaps(): Plugin {
  return {
    name: "aad:remove-remote-environment-maps",
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

/**
 * zod, through ext-apps, probes for eval with `new Function("")` and falls back when it throws. A strict
 * host still reports that as a CSP violation, and the viewer must not use eval at all (CONTRACTS section 8).
 */
function removeZodEvalProbe(): Plugin {
  const allowsEval = /export const allowsEval = [^\n]*cached\(\(\) => \{[\s\S]*?\n\}\);/;
  const compile = /const F = Function;[\s\S]*?return factory\(\.\.\.Object\.values\(this\.closed\)\);/;
  return {
    name: "aad:remove-zod-eval-probe",
    enforce: "pre",
    transform(code, id) {
      if (!/zod[\\/]v4[\\/]core[\\/](util|doc)\.js$/.test(id)) return null;
      const next = code
        .replace(allowsEval, "export const allowsEval = { value: false };")
        .replace(compile, 'throw new Error("AAD builds zod without eval.");');
      return next === code ? null : { code: next, map: null };
    },
  };
}

export default defineConfig({
  plugins: [removeRemoteEnvironmentMaps(), removeZodEvalProbe(), viteSingleFile({ removeViteModuleLoader: true })],
  build: {
    outDir: "dist",
    target: "es2022",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    chunkSizeWarningLimit: 4000,
  },
});
