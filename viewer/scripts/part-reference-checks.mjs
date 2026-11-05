// Real double-clicks against the real viewer, read from the actual postMessage wire. A reference
// reaches the real chat composer unsent; the viewer has no prompt field of its own.
export const COMPOSER_CAPABILITIES = {
  experimental: { "openai/message": {} }, message: { text: {} },
  serverTools: {}, updateModelContext: { text: {}, structuredContent: {} },
};

const uriFor = (scene, path) => `aad://part/${encodeURIComponent(scene.project)}/${scene.rev}/${scene.scene_id}?path=${encodeURIComponent(path)}`;
const copiedFor = (scene, path, label) => `AAD part ${label}: ${uriFor(scene, path)} `;

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

const message = frame => frame.locator(".aad-reference-message");
const waitForMessage = (frame, pattern) => frame.waitForFunction(source => new RegExp(source).test(document.querySelector(".aad-reference-message").textContent)
  && !document.querySelector(".aad-reference").hidden, pattern.source);
// The hidden CAD engine owns its own controls; everything AAD adds must be free of text entry.
const noEditor = frame => frame.evaluate(() => ![...document.querySelectorAll("textarea, input, [contenteditable]")].some(e => !e.closest(".aad-cad")));
const sendResult = (host, result) => host.page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), result);
const blockClipboard = frame => frame.evaluate(() => {
  document.execCommand = () => false;
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
});
const selectedText = frame => frame.evaluate(() => getSelection().toString());

/** Reads the system clipboard from a separate granted page. The viewer's own frame has no grant. */
async function readClipboard(host) {
  const context = host.page.context(), origin = "https://clipboard.test";
  await context.grantPermissions(["clipboard-read"], { origin });
  await context.route(`${origin}/`, route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>clipboard</title>" }));
  const reader = await context.newPage();
  try {
    await reader.goto(`${origin}/`);
    await reader.bringToFront();
    return await reader.evaluate(() => navigator.clipboard.readText());
  } finally { await reader.close(); await host.page.bringToFront(); }
}

export async function checkCopiedReference({ host, mcp, check }) {
  const frame = host.frame(), page = host.page, scene = mcp.toolResult.structuredContent.scene;
  const path = scene.parts[0].path, point = await canvasPoint(host, path);
  check((await host.hostState()).messages.every(m => m.method !== "ui/message"), "a host without the extension gets no message on load");
  check(!(await frame.evaluate(() => globalThis.__aad.host.composerReferences)), "ordinary ui/message support is not composer support");
  check(/copy a reference/.test(await frame.locator(".aad-hint").innerText()), "the hint promises copying, not insertion");

  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(() => window.__host.modelContext.at(-1)?.structuredContent?.selection?.length === 1);
  check(await frame.locator(".aad-reference").isHidden(), "single click selects without any reference status");
  const selection = (await host.hostState()).modelContext.at(-1).structuredContent.selection[0];
  check(selection.kind === "part" && selection.selector === uriFor(scene, path), "selection context refers to the whole exact part");

  const sized = (await host.hostState()).sized;
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Copied /);
  check(await noEditor(frame), "double-click opens no editor and leaves no copy field behind");
  const label = scene.parts[0].name;
  const clipboard = await readClipboard(host);
  check(clipboard === copiedFor(scene, path, label), "real copy in the sandboxed frame holds the exact identity, ending ready for the request", JSON.stringify(clipboard));
  check(!clipboard.includes("@") && !(await message(frame).innerText()).includes("@"), "copied text and its status have no bare @ for a composer to read as a file");
  check((await host.hostState()).sent.length === 0, "double-click on a host without the extension sends nothing");
  check(await frame.locator(".aad-reference-text").isHidden() && await frame.locator('[data-control="copy-reference"]').isHidden(), "a successful copy shows status only");
  check((await host.hostState()).sized === sized, "reference status does not resize the viewer");

  await page.waitForTimeout(600);
  await frame.evaluate(() => {
    globalThis.__realExec = document.execCommand;
    globalThis.__realClipboard = navigator.clipboard;
  });
  await blockClipboard(frame);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Could not copy .*⌘C/);
  check(!/^Copied/.test(await message(frame).innerText()), "blocked copying makes no success claim");
  const expected = copiedFor(scene, path, label);
  check((await frame.locator(".aad-reference-text").textContent()) === expected, "blocked copying shows the exact reference");
  check(await selectedText(frame) === expected, "the reference is selected for manual copying");
  check(await noEditor(frame), "the manual fallback is selected text, not a text editor");
  check((await host.hostState()).sent.length === 0, "blocked copying still sends nothing");

  await frame.getByRole("button", { name: "Copy", exact: true }).click();
  await page.waitForTimeout(50);
  check(/^Could not copy/.test(await message(frame).innerText()) && await selectedText(frame) === expected, "Copy reports failure again and reselects the text while blocked");

  // A newer revision with identical bytes must retire the old reference.
  const nextScene = { ...scene, rev: scene.rev + 1 };
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, rev: nextScene.rev, scene: nextScene } });
  await frame.locator(".aad-reference").waitFor({ state: "hidden" });
  check(true, "a new revision removes the stale reference and its Copy action");
  await frame.evaluate(() => {
    document.execCommand = globalThis.__realExec;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: globalThis.__realClipboard });
  });
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Copied /);
  check(await readClipboard(host) === copiedFor(nextScene, path, label), "the same mesh's new revision gets a new exact reference");
  check(await frame.evaluate(() => globalThis.__aad.loads === 1), "references never reload or change the mesh");

  // Double-clicking an unlisted part makes nothing, rather than guessing from its name.
  const unlisted = { ...nextScene, parts: nextScene.parts.filter(part => part.path !== path) };
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, rev: nextScene.rev, scene: unlisted } });
  await frame.evaluate(() => { globalThis.__copies = 0; const real = document.execCommand; document.execCommand = (...args) => { globalThis.__copies++; return real.apply(document, args); }; });
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /not in the current model/);
  check(await frame.evaluate(() => globalThis.__copies === 0), "a part missing from the current scene copies no reference");
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, rev: nextScene.rev, scene: nextScene } });

  const corner = await frame.locator(".aad-stage").evaluate(e => { const s = getComputedStyle(e); return [s.borderRadius, s.clipPath]; });
  check(corner.every(value => value.includes("28px")), "the canvas and all its content follow the rounded viewer corners");
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, scene: nextScene, viewer: { mode: "inline", presentation: { style: "wireframe" } } } });
  await frame.waitForFunction(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 0 && s[1] === 1));
  const edge = await canvasPoint(host, path);
  await page.mouse.click(edge.x, edge.y);
  await page.waitForTimeout(150);
  check(await frame.evaluate(() => [...globalThis.__viewer.rendered.nestedGroup.highlight.data].some(flags => flags & 1)), "whole-part selection persists when only wireframe edges are visible");
}

export async function checkComposerReference({ host, mcp, check }) {
  const frame = host.frame(), page = host.page, scene = mcp.toolResult.structuredContent.scene;
  const path = scene.parts[0].path, point = await canvasPoint(host, path);
  check(await frame.evaluate(() => globalThis.__aad.host.composerReferences === true), "the host's experimental capability survives SDK initialization");
  check(/add it to your message/.test(await frame.locator(".aad-hint").innerText()), "the hint names the composer on a supporting host");
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(() => window.__host.modelContext.length > 0);
  check((await host.hostState()).sent.length === 0, "single click never touches the composer");
  await page.waitForTimeout(600);
  await frame.evaluate(() => { globalThis.__copies = 0; const real = document.execCommand; document.execCommand = (...args) => { globalThis.__copies++; return real.apply(document, args); }; });

  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Added /);
  const { sent } = await host.hostState();
  const mention = (await message(frame).innerText()).match(/@\S+/)[0];
  check(mention === `@${scene.parts[0].name}`, "the native composer title keeps the @ label", mention);
  check(sent.length === 1, "double-click makes exactly one composer request", String(sent.length));
  check(JSON.stringify(sent[0]) === JSON.stringify({
    role: "user",
    content: [{ type: "text", text: `AAD part reference: ${uriFor(scene, path)}`, _meta: { "openai/title": mention } }],
    _meta: { "openai/message": { target: "active", send: false } },
  }), "the wire carries a titled reference with the exact identity, target active and send false", JSON.stringify(sent[0]));
  check(await frame.evaluate(() => globalThis.__copies === 0), "a supporting host leaves the clipboard alone");
  check(await noEditor(frame), "composer insertion opens no editor");
  check(!/focus/i.test(await message(frame).innerText()), "status does not claim composer focus");
}

export async function checkRejectedComposerReference({ host, mcp, check }) {
  const frame = host.frame(), page = host.page, scene = mcp.toolResult.structuredContent.scene;
  const path = scene.parts[0].path, point = await canvasPoint(host, path);
  const respondWith = response => page.evaluate(response => { window.__host.messageResponse = response; }, response);
  await page.waitForTimeout(600);

  // Fast rejection: the double-click's activation is still live.
  await respondWith({ error: { code: -32000, message: "Rejected" } });
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Could not add .*Copied a reference instead/);
  const expected = copiedFor(scene, path, scene.parts[0].name);
  const fallback = await readClipboard(host);
  check(fallback === expected && !fallback.includes("@"), "host rejection falls back to a real copy with no bare @", JSON.stringify(fallback));
  check((await host.hostState()).sent.length === 1, "host rejection is never retried as a message");

  // isError result, with copying unavailable: offer the reference, never pretend.
  await page.waitForTimeout(600);
  await respondWith({ result: { isError: true } });
  await frame.evaluate(() => {
    globalThis.__realExec = document.execCommand;
    globalThis.__realClipboard = navigator.clipboard;
  });
  await blockClipboard(frame);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Could not add .*Copy it/);
  const state = await host.hostState();
  check(state.sent.length === 2 && state.sent.every(params => params._meta["openai/message"].send === false), "every attempt stays unsent", String(state.sent.length));
  check(await selectedText(frame) === expected && await noEditor(frame), "an isError result offers selected reference text without an editor");

  await frame.evaluate(() => {
    document.execCommand = globalThis.__realExec;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: globalThis.__realClipboard });
  });
  await frame.getByRole("button", { name: "Copy", exact: true }).click();
  await waitForMessage(frame, /^Copied /);
  check(await readClipboard(host) === expected, "Copy recovers from the host failure with a fresh click");
  check((await host.hostState()).sent.length === 2, "Copy sends nothing");

  // A rejection for an old revision must not offer that revision's reference.
  await page.waitForTimeout(600);
  await respondWith({ error: { code: -32000, message: "Rejected" } });
  await page.evaluate(() => { window.__host.messageDelayMs = 300; });
  const nextScene = { ...scene, rev: scene.rev + 1 };
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^Adding /);
  await sendResult(host, { ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, rev: nextScene.rev, scene: nextScene } });
  await page.waitForFunction(count => window.__host.sent.length === count, 3);
  await page.waitForTimeout(600);
  const shown = await frame.locator(".aad-reference-text").evaluate(e => ({ hidden: e.hidden, text: e.textContent }));
  check(shown.hidden && !shown.text && await frame.locator(".aad-reference").isHidden(), "a late rejection for an old revision never surfaces its reference", JSON.stringify(shown));
}

export async function checkUnsupportedExtensionShape({ host, mcp, check }) {
  const frame = host.frame(), page = host.page;
  const point = await canvasPoint(host, mcp.toolResult.structuredContent.scene.parts[0].path);
  check(await frame.evaluate(() => !globalThis.__aad.host.composerReferences), "the extension without message.text is not composer support");
  await page.waitForTimeout(600);
  await page.mouse.dblclick(point.x, point.y);
  await waitForMessage(frame, /^(Copied|Could not copy) /);
  check((await host.hostState()).sent.length === 0, "that host never receives ui/message");
}
