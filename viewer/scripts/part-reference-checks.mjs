// Integration checks for selection context, the unsent draft, and opaque-frame copying.
export async function checkPartDraft({ host, mcp, check }) {
  const frame = host.frame(), page = host.page;
  const canvasPoint = async path => {
    const point = await frame.evaluate(path => {
      const v = globalThis.__viewer, rect = v.renderer.domElement.getBoundingClientRect();
      v.idPicker.setDirty();
      for (let y = 64; y < rect.height - 56; y += 8) for (let x = 24; x < rect.width - 24; x += 8) {
        const hit = v.idPicker.pickAt(x, y, { windowSize: 1 });
        if (hit && (hit.info.solidPath ?? hit.info.path.replace(/\/(faces|edges|vertices)\/[^/]+$/, "")) === path && v.pickingController.pickVisible(hit.info)) return { x: x + rect.left, y: y + rect.top };
      }
    }, path);
    if (!point) throw new Error(`No visible pick point for ${path}`);
    const offset = await page.locator("#f").boundingBox();
    return { x: point.x + offset.x, y: point.y + offset.y };
  };
  const path = mcp.toolResult.structuredContent.scene.parts[0].path;
  const originalHeight = (await host.hostState()).sized;
  const point = await canvasPoint(path);
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(() => window.__host.modelContext.at(-1)?.structuredContent?.selection?.length === 1);
  check(await frame.locator(".aad-draft").isHidden(), "single click selects a part without opening a draft");
  const firstContext = (await host.hostState()).modelContext.at(-1);
  check(firstContext.structuredContent.selection[0].kind === "part" && new URL(firstContext.structuredContent.selection[0].selector).searchParams.get("path") === path,
    "selection context refers to the whole exact part");
  await page.mouse.dblclick(point.x, point.y);
  await frame.getByRole("textbox", { name: "Part request", exact: true }).fill("make this wall thicker");
  check(await frame.locator(".aad-draft").isVisible(), "double-click opens an unsent part draft");
  check((await frame.locator(".aad-draft-reference").innerText()).startsWith("@"), "the draft starts with a part mention");
  await page.waitForFunction(height => window.__host.sized > height + 100, originalHeight);
  check((await host.hostState()).sent.length === 0, "opening and editing a part draft never sends a user message");
  await frame.evaluate(() => {
    document.execCommand = command => {
      if (command !== "copy") return false;
      globalThis.__copiedDraft = document.activeElement.value;
      return true;
    };
  });
  await frame.getByRole("button", { name: "Copy draft", exact: true }).click();
  let copied = await frame.evaluate(() => globalThis.__copiedDraft);
  check(copied.includes("make this wall thicker") && copied.includes("aad://part/") && new URL(copied.split("AAD part: ")[1]).searchParams.get("path") === path,
    "Copy includes the request and its exact model reference");

  const nextScene = { ...mcp.toolResult.structuredContent.scene, rev: mcp.toolResult.structuredContent.scene.rev + 1 };
  await page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), {
    ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, rev: nextScene.rev, scene: nextScene },
  });
  await frame.getByRole("button", { name: "Copy draft", exact: true }).waitFor({ state: "visible" });
  await frame.waitForFunction(() => document.querySelector('[data-control="copy-draft"]').disabled);
  check(await frame.getByRole("textbox", { name: "Part request", exact: true }).inputValue() === "make this wall thicker", "a new revision preserves the unsent text and marks its part reference stale");
  await page.mouse.dblclick(point.x, point.y);
  await frame.waitForFunction(() => !document.querySelector('[data-control="copy-draft"]').disabled);
  await frame.getByRole("button", { name: "Copy draft", exact: true }).click();
  copied = await frame.evaluate(() => globalThis.__copiedDraft);
  check(new URL(copied.split("AAD part: ")[1]).pathname.includes(`/${nextScene.rev}/`), "re-selecting the part updates its reference even when geometry bytes are identical");
  check(await frame.evaluate(() => globalThis.__aad.loads === 1), "draft references do not reload or change the mesh");

  await frame.evaluate(() => { document.execCommand = () => false; Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined }); });
  await frame.getByRole("button", { name: "Copy draft", exact: true }).click();
  const manual = frame.getByRole("textbox", { name: "Draft to copy", exact: true });
  await manual.waitFor({ state: "visible" });
  check(await manual.evaluate(e => e.readOnly && e.selectionStart === 0 && e.selectionEnd === e.value.length && e.value.includes("aad://part/")), "blocked clipboard access offers selected text for manual copy");
  check((await host.hostState()).sent.length === 0, "copy and clipboard fallback never auto-submit a prompt");
  const corner = await frame.locator(".aad-stage").evaluate(e => { const s = getComputedStyle(e); return [s.borderRadius, s.clipPath]; });
  check(corner.every(value => value.includes("28px")), "the canvas and all its content follow the rounded viewer corners");
  await frame.getByRole("button", { name: "Close draft", exact: true }).click();
  check(await frame.locator(".aad-draft").isHidden(), "closing the draft returns to the simple viewer");
  await page.evaluate(result => document.getElementById("f").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }, "*"), {
    ...mcp.toolResult, structuredContent: { ...mcp.toolResult.structuredContent, scene: nextScene, viewer: { mode: "inline", presentation: { style: "wireframe" } } },
  });
  await frame.waitForFunction(() => Object.values(globalThis.__viewer.getStates()).every(s => s[0] === 0 && s[1] === 1));
  const edge = await canvasPoint(path);
  await page.mouse.click(edge.x, edge.y);
  await page.waitForTimeout(150);
  check(await frame.evaluate(() => [...globalThis.__viewer.rendered.nestedGroup.highlight.data].some(flags => flags & 1)), "whole-part selection persists when only wireframe edges are visible");
}
