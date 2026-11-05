// Real input in the sandboxed viewer; composer requests must remain unsent.
export const COMPOSER_CAPABILITIES = {
  experimental: { "openai/message": {} }, message: { text: {} },
  serverTools: {}, updateModelContext: { text: {}, structuredContent: {} },
};
const uriFor = (scene, path) => `aad://part/${encodeURIComponent(scene.project)}/${scene.rev}/${scene.scene_id}?path=${encodeURIComponent(path)}`;
async function canvasPoint(host, path) {
  const point = await host.frame().evaluate(path => {
    const v = globalThis.__viewer, rect = v.renderer.domElement.getBoundingClientRect();
    v.idPicker.setDirty();
    for (let y = 64; y < rect.height - 56; y += 8) for (let x = 24; x < rect.width - 24; x += 8) {
      const hit = v.idPicker.pickAt(x, y, { windowSize: 1 });
      if (hit && (hit.info.solidPath ?? hit.info.path.replace(/\/(faces|edges|vertices)\/[^/]+$/, "")) === path && v.pickingController.pickVisible(hit.info)) return { x: x + rect.left, y: y + rect.top };
    }
  }, path);
  if (!point) throw new Error(`No visible pick point for ${path}`);
  const offset = await host.page.locator("#f").boundingBox();
  return { x: point.x + offset.x, y: point.y + offset.y };
}
const message = frame => frame.locator(".aad-reference");
const waitForMessage = (frame, pattern) => frame.waitForFunction(source => new RegExp(source).test(document.querySelector(".aad-reference").textContent) && !document.querySelector(".aad-reference").hidden, pattern.source);
const noEditor = frame => frame.evaluate(() => ![...document.querySelectorAll("textarea, input, [contenteditable]")].some(e => !e.closest(".aad-cad")));
const sendResult = (host, result) => host.page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), result);
const watchClipboard = frame => frame.evaluate(() => {
  globalThis.__copies = 0;
  document.execCommand = () => { globalThis.__copies++; return false; };
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { globalThis.__copies++; } } });
});

export async function checkUnavailableReference({ host, mcp, check }) {
  const frame = host.frame(), page = host.page, scene = mcp.toolResult.structuredContent.scene;
  const path = scene.parts[0].path, point = await canvasPoint(host, path);
  check(!await frame.evaluate(() => globalThis.__aad.host.composerReferences), "ordinary message support does not enable prompt insertion");
  check(!/Double-click|paste|copy/i.test(await frame.locator(".aad-hint").innerText()), "unsupported hosts offer no edit or paste workflow");
  await watchClipboard(frame);
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(() => window.__host.modelContext.at(-1)?.structuredContent?.selection?.length === 1);
  const selection = (await host.hostState()).modelContext.at(-1).structuredContent.selection[0];
  check(selection.kind === "part" && selection.selector === uriFor(scene, path), "single-click context identifies the whole exact part");
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  check((await host.hostState()).sent.length === 0 && await frame.evaluate(() => globalThis.__copies === 0), "unsupported double-click neither sends nor copies");
  check(await message(frame).isHidden() && await noEditor(frame), "unsupported double-click opens no editor or paste instructions");
  const corner = await frame.locator(".aad-stage").evaluate(e => { const s = getComputedStyle(e); return [s.borderRadius, s.clipPath]; });
  check(corner.every(value => value.includes("28px")), "all viewer content follows the rounded corners");
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, viewer: { mode: "inline", presentation: { style: "wireframe" } } } });
  await frame.waitForFunction(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 0 && s[1] === 1));
  const edge = await canvasPoint(host, path);
  await page.mouse.click(edge.x, edge.y);
  await page.waitForTimeout(150);
  check(await frame.evaluate(() => [...globalThis.__viewer.rendered.nestedGroup.highlight.data].some(flags => flags & 1)), "whole-part selection persists with only wireframe edges");
}

export async function checkComposerReference({ host, mcp, check }) {
  const frame = host.frame(), page = host.page, scene = mcp.toolResult.structuredContent.scene;
  const path = scene.parts[0].path, point = await canvasPoint(host, path);
  check(await frame.evaluate(() => globalThis.__aad.host.composerReferences === true), "composer support survives SDK initialization");
  check(/Double-click to add it/.test(await frame.locator(".aad-hint").innerText()), "supporting hosts offer native prompt insertion");
  await watchClipboard(frame);
  await page.mouse.click(point.x, point.y);
  check((await host.hostState()).sent.length === 0, "single-click never touches the composer");
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Added /);
  const { sent } = await host.hostState(), mention = `@${scene.parts[0].name}`;
  check(sent.length === 1, "double-click requests exactly one unsent reference");
  check(JSON.stringify(sent[0]) === JSON.stringify({ role: "user", content: [{ type: "text", text: `AAD part reference: ${uriFor(scene, path)}`, _meta: { "openai/title": mention } }], _meta: { "openai/message": { target: "active", send: false } } }), "the actual wire preserves exact identity and send:false", JSON.stringify(sent[0]));
  check(await frame.evaluate(() => globalThis.__copies === 0) && await noEditor(frame), "native insertion uses no clipboard or separate editor");
  const nextScene = { ...scene, rev: scene.rev + 1 };
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, rev: nextScene.rev, scene: nextScene } });
  await message(frame).waitFor({ state: "hidden" });
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Added /);
  check((await host.hostState()).sent.at(-1).content[0].text.endsWith(uriFor(nextScene, path)), "same-mesh revision changes update the reference identity");
  const unlisted = { ...nextScene, parts: nextScene.parts.filter(part => part.path !== path) };
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, rev: nextScene.rev, scene: unlisted } });
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /not in the current model/);
  check((await host.hostState()).sent.length === 2, "an unlisted part gets no guessed reference");
  check(await frame.evaluate(() => globalThis.__aad.loads === 1), "references never reload the original mesh");
}

export async function checkRejectedComposerReference({ host, mcp, check }) {
  const frame = host.frame(), page = host.page, scene = mcp.toolResult.structuredContent.scene;
  const point = await canvasPoint(host, scene.parts[0].path);
  await watchClipboard(frame);
  for (const response of [{ error: { code: -32000, message: "Rejected" } }, { result: { isError: true } }]) {
    await page.evaluate(response => { window.__host.messageResponse = response; }, response);
    await page.waitForTimeout(600);
    await page.mouse.dblclick(point.x, point.y);
    await waitForMessage(frame, /^Could not add /);
  }
  const state = await host.hostState();
  check(state.sent.length === 2 && state.sent.every(params => params._meta["openai/message"].send === false), "host errors never trigger a send fallback");
  check(await frame.evaluate(() => globalThis.__copies === 0) && await noEditor(frame), "host errors offer no paste workflow or separate editor");
  await page.evaluate(() => { window.__host.messageDelayMs = 300; });
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, scene: { ...scene, rev: scene.rev + 1 } } });
  await message(frame).waitFor({ state: "hidden" });
  await page.waitForTimeout(350);
  check(await message(frame).isHidden(), "a late error cannot revive an old revision's reference");
}

export async function checkUnsupportedExtensionShape({ host, mcp, check }) {
  const frame = host.frame(), page = host.page;
  check(!await frame.evaluate(() => globalThis.__aad.host.composerReferences), "the extension without text support is unavailable");
  const point = await canvasPoint(host, mcp.toolResult.structuredContent.scene.parts[0].path);
  await page.mouse.dblclick(point.x, point.y);
  check((await host.hostState()).sent.length === 0 && await message(frame).isHidden(), "incomplete capabilities expose no prompt action");
}
