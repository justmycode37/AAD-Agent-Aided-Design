import { gunzipToText, paintLoading, readScene } from "./loader";
import type { Envelope, Transport, NamedView, PartReference, SceneManifest } from "./transport/types";
import { mountCad3d, type Cad3dView } from "./viewer3d";
import { applyPresentation } from "./presentation";
import { navigationHint } from "./navigation";
import { installPartSelection, type PartSelection } from "./part-selection";
import { partReference, partReferenceUri } from "./part-reference";
import "./style.css";

const STAGE_HEIGHT = 480;
type Diagnostics = {
  state: "boot" | "loading" | "ready" | "error";
  ms?: Record<string, number>; load?: unknown; rawBytes?: number; error?: string;
  host?: unknown; shownScene?: string; loads: number; triangles?: number; verified?: boolean;
};
export const diagnostics: Diagnostics = { state: "boot", loads: 0 };
(globalThis as { __aad?: Diagnostics }).__aad = diagnostics;

function resolvedTheme(transport: Transport): "light" | "dark" {
  return transport.theme() ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

/** Count the original triangles without rewriting any geometry buffers. */
export function triangleCount(scene: any): number {
  const visit = (node: any): number => {
    const shape = node.shape?.ref !== undefined ? scene.instances[node.shape.ref] : node.shape;
    const triangles = shape?.triangles;
    const count = Array.isArray(triangles) ? triangles.length / 3 : triangles?.buffer ? atob(triangles.buffer).length / 12 : 0;
    return count + (node.parts ?? []).reduce((total: number, part: any) => total + visit(part), 0);
  };
  return visit(scene.shapes ?? scene);
}

const icon = (paths: string) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;

export async function runApp(transport: Transport, root: HTMLElement): Promise<void> {
  const referenceHint = transport.caps.composerReferences ? " · Double-click to add it to your message" : "";
  root.innerHTML = `
    <section class="aad-frame" aria-label="3D model">
      <div class="aad-stage">
        <div class="aad-cad"></div>
        <div class="aad-heading">
          <div class="aad-caption">3D model</div>
          <fieldset class="aad-toolbar" aria-label="View the model" disabled>
            <button class="aad-views-button" type="button" data-control="views" aria-label="Views" title="Views" aria-haspopup="menu" aria-expanded="false" aria-controls="aad-view-menu">${icon('<path d="m3 7 9-5 9 5-9 5Zm0 5 9 5 9-5M3 17l9 5 9-5"/>')}<span>Views</span></button>
            <button type="button" data-control="explode" aria-label="Pull apart" title="Pull apart" aria-pressed="false">${icon('<path d="m3 8 4-2 4 2v7l-4 2-4-2Zm10 1 4-2 4 2v7l-4 2-4-2M7 10v7m10-6v7M3 8l4 2 4-2m2 1 4 2 4-2"/>')}</button>
            <button type="button" data-control="grid" aria-label="Show grid" title="Show grid" aria-pressed="false">${icon('<path d="m3 15 9-5 9 5-9 5Zm4-2 9 5M7 17l9-5M12 10V3m0 0-2 2m2-2 2 2"/>')}</button>
            <button type="button" data-control="reset" aria-label="Reset" title="Reset">${icon('<path d="M4 10a8 8 0 1 1 1 9M4 10V4m0 6h6"/>')}</button>
          </fieldset>
          <div id="aad-view-menu" class="aad-view-menu" role="menu" aria-label="Model views" hidden></div>
        </div>
        <div class="aad-part-label" hidden></div>
        <button class="aad-expand" type="button" data-control="fullscreen" aria-label="Make bigger" title="Make bigger" hidden>${icon('<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>')}</button>
        <div class="aad-progress" role="progressbar" aria-label="Loading model" aria-valuemin="0" aria-valuemax="100" data-indeterminate><span></span></div>
        <div class="aad-reference" role="status" hidden></div>
        <div class="aad-analysis" hidden><div class="aad-analysis-title"></div><div class="aad-analysis-ramp"></div><div class="aad-analysis-range"></div><div class="aad-analysis-source"></div></div>
      </div>
      <p class="aad-hint">${navigationHint()}${transport.controls === "cad" ? "" : `<span>Click a part to select${referenceHint}</span>`}</p>
      <p class="aad-error" role="alert" hidden></p>
    </section>`;
  const q = <T extends HTMLElement>(s: string) => root.querySelector(s) as T;
  const loadingProgress = (fraction?: number) => {
    const bar = q(".aad-progress");
    bar.toggleAttribute("data-indeterminate", fraction === undefined);
    bar.setAttribute("aria-label", fraction === undefined ? "Preparing model" : "Loading model");
    if (fraction === undefined) {
      bar.removeAttribute("aria-valuenow");
      bar.querySelector<HTMLElement>("span")!.style.removeProperty("transform");
    } else {
      bar.setAttribute("aria-valuenow", String(Math.round(fraction * 100)));
      bar.querySelector<HTMLElement>("span")!.style.transform = `scaleX(${fraction})`;
    }
  };
  const control = <T extends HTMLElement>(name: string) => q<T>(`[data-control="${name}"]`);
  q(".aad-frame").dataset.theme = resolvedTheme(transport);
  q(".aad-frame").dataset.controls = transport.controls ?? "simple";
  const fail = (error: unknown) => {
    diagnostics.state = "error"; diagnostics.error = String((error as Error)?.stack ?? error);
    q(".aad-part-label").hidden = true;
    q(".aad-progress").hidden = true; q(".aad-error").hidden = false;
    q(".aad-error").textContent = `Could not open this model: ${(error as Error)?.message ?? error}`;
    console.error(error);
  };
  let view: Cad3dView | undefined;
  let selection: PartSelection | undefined;
  let manifest: SceneManifest | undefined;
  let referenceAttempt = 0;
  let referenceTimer: number | undefined;
  const inserting = new Set<string>();
  const sameRevision = (a: SceneManifest, b: SceneManifest) => a.project === b.project && a.rev === b.rev && a.scene_id === b.scene_id;
  // Numbers belong to the original manifest, never the filtered view or GPU face IDs.
  let partLabels = new Map<string, string>();
  let hoveredPath: string | null = null;
  const showHoveredPart = (path: string | null) => {
    hoveredPath = path;
    const label = path ? partLabels.get(path) : undefined;
    const element = q(".aad-part-label");
    element.textContent = label ?? "";
    element.hidden = !label;
  };
  const stageSelection = (paths: string[]) => {
    if (!manifest || !transport.stageSelection) return;
    // A mesh path absent from the current scene has no identity to share.
    const listed = new Set(manifest.parts.map(part => part.path));
    const refs = paths.filter(path => listed.has(path)).map(path => partReference(manifest!, path));
    void transport.stageSelection(refs).catch(() => console.warn("AAD selection context could not be shared"));
  };
  const hideReference = () => {
    clearTimeout(referenceTimer);
    q(".aad-reference").hidden = true;
    q(".aad-reference").textContent = "";
  };
  const showReference = (message: string) => {
    clearTimeout(referenceTimer);
    q(".aad-reference").hidden = false;
    q(".aad-reference").textContent = message;
    referenceTimer = window.setTimeout(hideReference, 6000);
  };
  const referencePart = (path: string) => {
    if (!manifest || !transport.caps.composerReferences || !transport.insertPartReference) return;
    let reference: PartReference;
    try { reference = partReference(manifest, path); }
    catch {
      referenceAttempt++;
      showReference("This part is not in the current model, so no reference was made.");
      return;
    }
    const uri = partReferenceUri(reference);
    if (inserting.has(uri)) return;
    inserting.add(uri);
    const attempt = ++referenceAttempt;
    showReference(`Adding ${reference.mention} to your message…`);
    void transport.insertPartReference(reference).then(() => {
      if (attempt === referenceAttempt) showReference(`Added ${reference.mention} to your message.`);
    }).catch(() => {
      if (attempt === referenceAttempt) showReference(`Could not add ${reference.mention} to your message.`);
    }).finally(() => inserting.delete(uri));
  };
  const updateManifest = (next: SceneManifest) => {
    if (manifest && !sameRevision(manifest, next)) {
      referenceAttempt++;
      hideReference();
    }
    const keepHover = manifest && sameRevision(manifest, next) ? hoveredPath : null;
    manifest = structuredClone(next);
    partLabels = new Map(manifest.parts.map((part, index) => [part.path, `${part.name} · #${index + 1}`]));
    showHoveredPart(keepHover);
  };
  let requested: Partial<NonNullable<Envelope["viewer"]>> = {};
  let requestKey = "";
  let unsubscribe = () => {};
  let loading: Promise<void> = Promise.resolve();
  let presets: NamedView[] = [];
  let selectedId = "original";
  let scenePresented = false;
  let menuOpen = false;
  let menuAnimation: Animation | undefined;
  const closeMenu = (focus = false, animate = true) => {
    const menu = q(".aad-view-menu");
    if (focus) control("views").focus();
    if (!menuOpen && animate) return;
    menuOpen = false; menu.inert = true; menu.setAttribute("aria-hidden", "true");
    control("views").setAttribute("aria-expanded", "false");
    menuAnimation?.cancel(); menuAnimation = undefined;
    if (menu.hidden || !animate || matchMedia("(prefers-reduced-motion: reduce)").matches) { menu.hidden = true; return; }
    const animation = menu.animate([{ opacity: 1, transform: "translateY(0) scale(1)" }, { opacity: 0, transform: "translateY(-4px) scale(.98)" }],
      { duration: 100, easing: "ease-in", fill: "both" });
    menuAnimation = animation;
    void animation.finished.then(() => {
      if (menuAnimation !== animation || menuOpen) return;
      menu.hidden = true; menuAnimation = undefined; animation.cancel();
    }, () => {});
  };
  const applySelected = () => {
    if (!view) return;
    selection?.clear();
    const selected = presets.find(p => p.id === selectedId);
    // A scene's first recipe sets its grid. After that, grid and axes belong to
    // the user, so Views, Reset, and same-scene updates must not change them.
    const [xy, xz, yz] = view.viewer.getGrids();
    const axes = view.viewer.getAxes();
    const keepGrid = scenePresented;
    view.batch(() => {
      try {
        view!.reset();
        const zoom = view!.viewer.getCameraZoom();
        view!.viewer.presetCamera(selected?.view ?? requested.view ?? "iso");
        view!.viewer.setCameraZoom(zoom);
        applyPresentation(view!, { grid: false, explode: false, ...(selected ? selected.presentation : requested.presentation) });
        view!.setAnalysis(selected?.analysis);
        view!.fit();
        selection?.refresh();
      } finally {
        if (keepGrid) { view!.viewer.setGrids([xy, xz, yz]); view!.viewer.setAxes(axes); }
      }
    });
    scenePresented = true;
    const analysis = selected?.analysis;
    q(".aad-analysis").hidden = !analysis;
    if (analysis) {
      q(".aad-analysis-title").textContent = `${analysis.quantity} by part (${analysis.units})`;
      q(".aad-analysis-range").textContent = analysis.range.map(n => new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4 }).format(n)).join(" — ");
      q(".aad-analysis-source").textContent = `Source: ${analysis.source}`;
      q(".aad-analysis-source").title = analysis.source;
    }
    for (const item of q(".aad-view-menu").querySelectorAll<HTMLElement>("[data-view-id]")) {
      item.setAttribute("aria-checked", String(item.dataset.viewId === selectedId));
    }
    control("views").title = `Views: ${selected?.label ?? "Original"}`;
    syncControls();
  };
  const configureViews = () => {
    presets = requested.views ?? [];
    // Preserve old callers as a named view; no separate Interior button.
    if (requested.views === undefined && requested.presentation?.inside) {
      const { inside, ...base } = requested.presentation;
      presets = [{ id: "interior", label: "Interior", presentation: { ...base,
        hide: inside.hide ? [...(base.hide ?? []), ...inside.hide] : base.hide, section: inside.section ?? base.section } }];
    }
    if (!presets.some(p => p.id === selectedId)) selectedId = "original";
    closeMenu(false, false);
    q(".aad-view-menu").replaceChildren();
    for (const preset of [{ id: "original", label: "Original", description: "Prepared starting view" }, ...presets]) {
      const item = document.createElement("button");
      item.type = "button"; item.role = "menuitemradio"; item.tabIndex = -1; item.dataset.viewId = preset.id;
      item.setAttribute("aria-label", preset.label);
      const label = document.createElement("span"); label.textContent = preset.label; item.append(label);
      if (preset.description) { const description = document.createElement("small"); description.textContent = preset.description; item.append(description); }
      item.addEventListener("click", () => { selectedId = preset.id; applySelected(); closeMenu(true); });
      q(".aad-view-menu").append(item);
    }
    control<HTMLButtonElement>("views").disabled = !presets.length;
  };
  const syncControls = () => {
    if (!view) return;
    const exploded = view.isExploded();
    const grid = view.viewer.getGrids().some(Boolean);
    control("explode").setAttribute("aria-pressed", String(exploded));
    control("grid").setAttribute("aria-pressed", String(grid));
    const explodeLabel = exploded ? "Put together" : "Pull apart";
    const gridLabel = grid ? "Hide grid" : "Show grid";
    control("explode").setAttribute("aria-label", explodeLabel);
    control("grid").setAttribute("aria-label", gridLabel);
    control<HTMLButtonElement>("explode").disabled = !view.canExplode();
    control("explode").title = view.canExplode() ? explodeLabel : "This model has one visible piece";
    control("grid").title = gridLabel;
    const label = transport.displayMode?.() === "fullscreen" ? "Make smaller" : "Make bigger";
    control("fullscreen").setAttribute("aria-label", label); control("fullscreen").title = label;
  };
  const size = () => {
    const fullscreen = transport.displayMode?.() === "fullscreen" || transport.fillsWindow === true;
    const other = q(".aad-frame").offsetHeight - q(".aad-stage").offsetHeight;
    q(".aad-stage").style.height = `${fullscreen ? Math.max(300, innerHeight - other - 16) : transport.controls === "cad" ? 640 : STAGE_HEIGHT}px`;
    const bounds = q(".aad-cad").getBoundingClientRect(); view?.resize(bounds.width, bounds.height);
    if (!fullscreen) transport.requestSize(Math.ceil(document.documentElement.getBoundingClientRect().height));
    syncControls();
  };
  const applyRequest = (env: Envelope) => {
    if (!view) return;
    requested = structuredClone(env.viewer ?? {});
    q(".aad-caption").textContent = requested.title || env.project;
    configureViews();
    applySelected();
  };
  control("explode").addEventListener("click", () => {
    if (view) view.setExploded(!view.isExploded());
  });
  control("grid").addEventListener("click", () => { if (view) view.batch(() => view!.setGrid(!view!.viewer.getGrids().some(Boolean))); });
  control("views").addEventListener("click", () => {
    const menu = q(".aad-view-menu");
    if (menuOpen) { closeMenu(); return; }
    menuAnimation?.cancel(); menuAnimation = undefined;
    menuOpen = true; menu.hidden = false; menu.inert = false; menu.removeAttribute("aria-hidden");
    control("views").setAttribute("aria-expanded", "true");
    const heading = q(".aad-heading").getBoundingClientRect();
    const stage = q(".aad-stage").getBoundingClientRect();
    const left = control("views").getBoundingClientRect().left - heading.left;
    menu.style.left = `${Math.max(0, Math.min(left, stage.right - heading.left - menu.getBoundingClientRect().width - 12))}px`;
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const animation = menu.animate([{ opacity: 0, transform: "translateY(-6px) scale(.98)" }, { opacity: 1, transform: "translateY(0) scale(1)" }],
        { duration: 160, easing: "cubic-bezier(.16,1,.3,1)" });
      menuAnimation = animation;
      void animation.finished.then(() => { if (menuAnimation === animation) menuAnimation = undefined; }, () => {});
    }
    (menu.querySelector<HTMLElement>('[aria-checked="true"]') ?? menu.firstElementChild as HTMLElement)?.focus();
  });
  q(".aad-view-menu").addEventListener("keydown", event => {
    const items = [...q(".aad-view-menu").querySelectorAll<HTMLElement>("button")];
    let i = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape") { event.preventDefault(); closeMenu(true); return; }
    if (event.key === "Tab") { closeMenu(true); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    i = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (i + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[i]?.focus();
  });
  document.addEventListener("pointerdown", event => {
    if (!q(".aad-view-menu").contains(event.target as Node) && !control("views").contains(event.target as Node)) closeMenu();
  });
  control("reset").addEventListener("click", applySelected);
  control("fullscreen").hidden = !transport.caps.fullscreen;
  control("fullscreen").addEventListener("click", () => void transport.requestFullscreen().then(size).catch(fail));

  const present = async (env: Envelope) => {
    if (!env.scene) throw new Error(env.summary || "The result has no model.");
    const nextKey = JSON.stringify({ presentation: env.viewer?.presentation ?? {}, view: env.viewer?.view, title: env.viewer?.title, views: env.viewer?.views ?? [] });
    if (env.scene.scene_id === diagnostics.shownScene) {
      const changedRevision = !manifest || manifest.project !== env.scene.project || manifest.rev !== env.scene.rev;
      if (changedRevision) selection?.clear();
      updateManifest(env.scene);
      if (changedRevision) {
        stageSelection([]);
        q(".aad-caption").textContent = env.viewer?.title || env.project;
      }
      if (requestKey !== nextKey) { applyRequest(env); requestKey = nextKey; }
      return;
    }
    diagnostics.state = "loading";
    showHoveredPart(null);
    const nextScene = env.scene;
    if (nextScene.format !== "tcv-5") throw new Error(`Unsupported scene format ${nextScene.format}`);
    q<HTMLFieldSetElement>(".aad-toolbar").disabled = true;
    q(".aad-progress").hidden = false; q(".aad-error").hidden = true;
    loadingProgress(0);
    await paintLoading();
    const started = performance.now();
    const { bytes, stats } = await readScene(nextScene.scene_id, nextScene.total_bytes, transport.readChunk.bind(transport), (done, total) => {
      loadingProgress(done / total);
    });
    const readAt = performance.now();
    loadingProgress();
    await paintLoading();
    const text = await gunzipToText(bytes); const shapes = JSON.parse(text);
    const triangles = triangleCount(shapes);
    if (nextScene.triangles !== undefined && triangles !== nextScene.triangles) throw new Error(`Expected ${nextScene.triangles} triangles, got ${triangles}`);
    const parsedAt = performance.now(); unsubscribe(); selection?.dispose(); selection = undefined; view?.dispose(); q(".aad-cad").replaceChildren();
    updateManifest(nextScene); stageSelection([]);
    selectedId = "original";
    scenePresented = false;
    await paintLoading();
    const bounds = q(".aad-cad").getBoundingClientRect();
    view = mountCad3d(q(".aad-cad"), shapes, { width: bounds.width, height: bounds.height, theme: resolvedTheme(transport), view: env.viewer?.view, controls: transport.controls });
    (globalThis as { __viewer?: unknown }).__viewer = view.viewer;
    if (!view.fullControls) {
      view.viewer.renderer.domElement.tabIndex = 0;
      view.viewer.renderer.domElement.setAttribute("aria-label", `3D model. Drag to turn. Click a part to select or deselect.${transport.caps.composerReferences ? " Double-click to add it to your message." : ""}`);
      selection = installPartSelection(view.viewer as never, { onSelection: stageSelection, onReference: referencePart, onHover: showHoveredPart });
    }
    unsubscribe = view.viewer.state.subscribeAll(syncControls);
    applyRequest(env); requestKey = nextKey;
    diagnostics.shownScene = nextScene.scene_id; diagnostics.triangles = triangles; diagnostics.verified = true;
    diagnostics.load = stats; diagnostics.rawBytes = text.length;
    diagnostics.ms = { read: readAt - started, parse: parsedAt - readAt, mount: performance.now() - parsedAt, sinceToolResult: performance.now() - started };
    await paintLoading();
    diagnostics.loads++; diagnostics.state = "ready";
    console.info(`AAD viewer ready: ${triangles} triangles; ${nextScene.total_bytes} scene bytes`);
    q(".aad-progress").hidden = true; q<HTMLFieldSetElement>(".aad-toolbar").disabled = false;
    size();
  };
  const queue = (env: Envelope) => { loading = loading.then(() => present(env)).catch(fail); return loading; };
  transport.onUpdate(env => void queue(env)); new ResizeObserver(size).observe(q(".aad-frame"));
  transport.onThemeChange(() => { const theme = resolvedTheme(transport); q(".aad-frame").dataset.theme = theme; view?.setTheme(theme); size(); });
  addEventListener("resize", size);
  try { await queue(await transport.getInitialResult()); } catch (error) { fail(error); }
}
