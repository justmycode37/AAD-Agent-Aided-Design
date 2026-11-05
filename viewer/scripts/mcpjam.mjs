// Drives a running MCPJam 2.0.9 inspector headless: runs aad_show on a project, reports the viewer's state
// and any CSP violation, and saves a screenshot. Setup is in docs/host-matrix.md.
//   node scripts/mcpjam.mjs <project> <screenshot.png>
import { chromium } from "playwright";

const [project, shot] = process.argv.slice(2);
const browser = await chromium.launch({ headless: true, channel: "chromium", args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await (await browser.newContext({ viewport: { width: 1500, height: 950 } })).newPage();
const violations = [];
await page.addInitScript(() => {
  addEventListener("securitypolicyviolation", (e) => console.log("CSPEVENT " + JSON.stringify({ directive: e.violatedDirective, blocked: String(e.blockedURI).slice(0, 200) })));
});
page.on("console", (m) => { if (m.text().startsWith("CSPEVENT")) violations.push(m.text().slice(9)); });
await page.goto("http://127.0.0.1:6274/#app-builder", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(6000);
await page.locator("button").filter({ hasText: /^aad_show/ }).first().click({ timeout: 15000 });
await page.waitForTimeout(1500);
await page.getByPlaceholder("Enter project").first().fill(project);
await page.getByRole("button", { name: /^Run/ }).first().click({ timeout: 10000 });
let diagnostics;
const end = Date.now() + 90_000;
while (Date.now() < end) {
  for (const frame of page.frames()) {
    const found = await frame.evaluate(() => globalThis.__aad && JSON.parse(JSON.stringify(globalThis.__aad))).catch(() => null);
    if (found) diagnostics = found;
  }
  if (diagnostics && ["ready", "error"].includes(diagnostics.state)) break;
  await page.waitForTimeout(250);
}
await page.waitForTimeout(2500);
await page.screenshot({ path: shot });
console.log(JSON.stringify({ state: diagnostics?.state, ms: diagnostics?.ms, load: diagnostics?.load, error: diagnostics?.error, csp_violations: violations }));
await browser.close();
process.exit(diagnostics?.state === "ready" && violations.length === 0 ? 0 : 1);
