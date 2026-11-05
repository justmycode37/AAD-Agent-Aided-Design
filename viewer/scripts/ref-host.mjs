// Reference MCP Apps host for tests: a sandboxed iframe with the limits read from T3's bundle
// (sandbox="allow-scripts allow-forms", 256 KiB per app message, 16 requests in flight).
// Passing { strictCsp: true } adds the spec's default CSP (connect-src 'none').
import { chromium } from "playwright";

const STRICT_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; media-src data:; font-src data:; connect-src 'none'";

// T3's advertised shape: `ui/message` exists and always sends, whatever its metadata says.
const DEFAULT_CAPABILITIES = { openLinks: {}, serverTools: {}, serverResources: {}, logging: {}, message: { text: {} }, updateModelContext: { text: {}, structuredContent: {} }, downloadFile: {}, sandbox: {} };

const hostPage = (width, theme, displayMode, capabilities) => `<!doctype html><html><body style="margin:0;background:${theme === "dark" ? "#1e1f24" : "#fff"}">
<iframe id="f" src="/app" sandbox="allow-scripts allow-forms" allow="fullscreen" style="border:0;display:block;width:${width}px;height:320px"></iframe>
<script>
const f = document.getElementById("f");
const MAX_MESSAGE = 262144, MAX_IN_FLIGHT = 16;
let inFlight = 0, initialized = false;
const post = (m) => f.contentWindow.postMessage(m, "*");
const ctx = { theme: ${JSON.stringify(theme)}, displayMode: ${JSON.stringify(displayMode)}, availableDisplayModes: ["inline", "fullscreen"], containerDimensions: { maxHeight: 800, width: ${width} }, platform: "desktop" };
window.__host = { messages: [], maxInFlight: 0, sized: null, tooLarge: 0, modelContext: [], sent: [], messageResponse: { result: {} } };
window.__setDisplayMode = (mode) => { ctx.displayMode = mode; f.style.width = mode === "fullscreen" ? "100vw" : "${width}px"; f.style.height = mode === "fullscreen" ? "100vh" : (window.__host.sized ?? 320) + "px"; post({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { displayMode: mode } }); };
window.__setTheme = (theme) => { ctx.theme = theme; post({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { theme } }); };
addEventListener("message", async (e) => {
  if (e.source !== f.contentWindow) return;
  const m = e.data;
  if (!m || m.jsonrpc !== "2.0") return;
  const size = new TextEncoder().encode(JSON.stringify(m)).length;
  window.__host.messages.push({ method: m.method, size });
  if (size > MAX_MESSAGE) {
    window.__host.tooLarge++;
    if (m.id !== undefined) post({ jsonrpc: "2.0", id: m.id, error: { code: -32600, message: "Message too large." } });
    return;
  }
  if (m.method === "ui/initialize") {
    post({ jsonrpc: "2.0", id: m.id, result: { protocolVersion: "2026-01-26", hostInfo: { name: "aad-ref-host", version: "0" },
      hostCapabilities: ${JSON.stringify(capabilities)}, hostContext: ctx } });
    return;
  }
  if (m.method === "ping") { post({ jsonrpc: "2.0", id: m.id, result: {} }); return; }
  if (m.method === "ui/notifications/initialized") {
    if (!initialized) {
      initialized = true;
      post({ jsonrpc: "2.0", method: "ui/notifications/tool-input", params: { arguments: {} } });
      post({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: await window.hostToolResult() });
    }
    return;
  }
  if (m.method === "ui/notifications/size-changed") {
    if (typeof m.params.height === "number") { f.style.height = m.params.height + "px"; window.__host.sized = m.params.height; }
    return;
  }
  if (m.id === undefined) return;
  if (m.method === "ui/update-model-context") window.__host.modelContext.push(m.params);
  if (m.method === "ui/message") {
    window.__host.sent.push(m.params);
    const response = window.__host.messageResponse;
    setTimeout(() => post({ jsonrpc: "2.0", id: m.id, ...response }), window.__host.messageDelayMs ?? 0);
    return;
  }
  if (m.method === "ui/request-display-mode") { window.__setDisplayMode(m.params.mode); post({ jsonrpc: "2.0", id: m.id, result: { mode: m.params.mode } }); return; }
  if (inFlight >= MAX_IN_FLIGHT) { post({ jsonrpc: "2.0", id: m.id, error: { code: -32000, message: "Too many requests in flight." } }); return; }
  inFlight++;
  window.__host.maxInFlight = Math.max(window.__host.maxInFlight, inFlight);
  try { post({ jsonrpc: "2.0", id: m.id, result: await window.hostRequest(m.method, m.params ?? {}) }); }
  catch (err) { post({ jsonrpc: "2.0", id: m.id, error: { code: -32000, message: String(err) } }); }
  finally { inFlight--; }
});
</script></body></html>`;

/**
 * @param {object} o
 * @param {string} o.html viewer HTML
 * @param {object} o.toolResult MCP result for the initial tool call
 * @param {(method: string, params: object) => Promise<object>} o.onRequest answers tools/call and friends
 * @param {object} [o.capabilities] hostCapabilities to advertise; defaults to T3's
 */
export async function openRefHost({ html, toolResult, onRequest, width = 728, height = 800, strictCsp = false, gpu = false, theme = "dark", displayMode = "inline", capabilities = DEFAULT_CAPABILITIES }) {
  const args = gpu ? ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"] : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
  const browser = await chromium.launch({ headless: true, channel: "chromium", args });
  const page = await (await browser.newContext({ viewport: { width, height } })).newPage();
  const logs = [];
  page.on("console", (m) => { if (m.type() !== "debug") logs.push(`[${m.type()}] ${m.text().slice(0, 400)}`); });
  page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));
  page.on("requestfailed", (r) => logs.push(`[requestfailed] ${r.url().slice(0, 160)}`));
  const requests = [];
  page.on("request", (r) => { if (!r.url().startsWith("data:")) requests.push(r.url().slice(0, 160)); });
  await page.exposeFunction("hostToolResult", () => toolResult);
  await page.exposeFunction("hostRequest", (method, params) => onRequest(method, params));
  const document = strictCsp ? html.replace("<head>", '<head><meta http-equiv="Content-Security-Policy" content="' + STRICT_CSP + '">') : html;
  const escaped = document.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  await page.setContent(hostPage(width, theme, displayMode, capabilities).replace('src="/app"', () => 'srcdoc="' + escaped + '"'));
  const frame = () => page.frames().find((f) => f !== page.mainFrame());

  return {
    page, frame, logs, requests,
    /** Waits until the viewer reports ready or error, then returns its diagnostics. */
    async waitReady(timeoutMs = 60_000) {
      const end = Date.now() + timeoutMs;
      while (Date.now() < end) {
        const d = await frame()?.evaluate(() => globalThis.__aad && JSON.parse(JSON.stringify(globalThis.__aad))).catch(() => null);
        if (d?.state === "ready" || d?.state === "error") return d;
        await page.waitForTimeout(25);
      }
      throw new Error(`Viewer not ready after ${timeoutMs} ms\n${logs.join("\n")}`);
    },
    hostState: () => page.evaluate(() => window.__host),
    async close() { await browser.close(); },
  };
}
