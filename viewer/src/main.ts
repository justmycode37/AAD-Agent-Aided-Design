import { paintLoading } from "./loader";
import { diagnostics, runApp } from "./app";
import { McpTransport } from "./transport/mcp";
import { BundledTransport, type BundledScene, EmbeddedTransport, type EmbeddedScene, type WindowConfig, WindowTransport } from "./transport/page";

declare global {
  interface Window {
    __AAD_WINDOW__?: WindowConfig;
    __AAD_EMBEDDED__?: EmbeddedScene;
    __AAD_EMBEDDED_SCENE__?: BundledScene;
  }
}

async function transport() {
  if (window.__AAD_EMBEDDED_SCENE__) { await paintLoading(); return new BundledTransport(window.__AAD_EMBEDDED_SCENE__); }
  if (window.__AAD_EMBEDDED__) { await paintLoading(); return new EmbeddedTransport(window.__AAD_EMBEDDED__); }
  if (window.__AAD_WINDOW__) return new WindowTransport(window.__AAD_WINDOW__);
  const mcp = new McpTransport();
  await mcp.connect();
  return mcp;
}

try {
  const chosen = await transport();
  diagnostics.host = chosen.caps;
  await runApp(chosen, document.getElementById("aad-root") as HTMLElement);
} catch (error) {
  diagnostics.state = "error";
  diagnostics.error = String(error);
  const root = document.getElementById("aad-root");
  if (root) root.textContent = `Could not start the CAD viewer: ${(error as Error).message}`;
  console.error(error);
}
