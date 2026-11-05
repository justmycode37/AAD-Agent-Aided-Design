import { gunzipToText, readScene } from "./loader";
import type { Envelope, Transport } from "./transport/types";
import { mountCad3d, type Cad3dView } from "./viewer3d";
import { applyPresentation, resolveParts } from "./presentation";
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
  root.innerHTML = `
    <section class="aad-frame" aria-label="3D model">
      <div class="aad-stage">
        <div class="aad-cad"></div>
        <div class="aad-heading">
          <div class="aad-caption">3D model</div>
          <fieldset class="aad-toolbar" aria-label="View the model" disabled>
            <button type="button" data-control="explode" aria-label="Pull apart" title="Pull apart" aria-pressed="false">${icon('<path d="m3 8 4-2 4 2v7l-4 2-4-2Zm10 1 4-2 4 2v7l-4 2-4-2M7 10v7m10-6v7M3 8l4 2 4-2m2 1 4 2 4-2"/>')}</button>
            <button type="button" data-control="inside" aria-label="Show inside" title="Show inside" aria-pressed="false" hidden>${icon('<path d="m3 7 9-5 9 5v10l-9 5-9-5Zm0 0 9 5 9-5M12 12v10"/><path d="m8 10 4-2 4 2v6l-4 2-4-2Z"/>')}</button>
            <button type="button" data-control="grid" aria-label="Show grid" title="Show grid" aria-pressed="false">${icon('<path d="m3 15 9-5 9 5-9 5Zm4-2 9 5M7 17l9-5M12 10V3m0 0-2 2m2-2 2 2"/>')}</button>
            <button type="button" data-control="reset" aria-label="Reset" title="Reset">${icon('<path d="M4 10a8 8 0 1 1 1 9M4 10V4m0 6h6"/>')}</button>
          </fieldset>
        </div>
        <button class="aad-expand" type="button" data-control="fullscreen" aria-label="Make bigger" title="Make bigger" hidden>${icon('<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>')}</button>
        <div class="aad-progress" role="status">Opening your model…<progress max="100" value="0"></progress></div>
      </div>
      <p class="aad-hint">Drag to turn · Scroll or pinch to zoom</p>
      <p class="aad-error" role="alert" hidden></p>
    </section>`;
  const q = <T extends HTMLElement>(s: string) => root.querySelector(s) as T;
  const control = <T extends HTMLElement>(name: string) => q<T>(`[data-control="${name}"]`);
  q(".aad-frame").dataset.theme = resolvedTheme(transport);
  const fail = (error: unknown) => {
    diagnostics.state = "error"; diagnostics.error = String((error as Error)?.stack ?? error);
    q(".aad-progress").hidden = true; q(".aad-error").hidden = false;
    q(".aad-error").textContent = `Could not open this model: ${(error as Error)?.message ?? error}`;
    console.error(error);
  };
  let view: Cad3dView | undefined;
  let requested: Partial<NonNullable<Envelope["viewer"]>> = {};
  let requestKey = "";
  let unsubscribe = () => {};
  let loading: Promise<void> = Promise.resolve();
  let insideShown = false;
  let insideAvailable = false;
  let outsideCamera: ReturnType<Cad3dView["viewer"]["getCameraLocationSettings"]> | undefined;
  let outsideExploded = false;
  const configureInside = () => {
    insideShown = false; outsideCamera = undefined; outsideExploded = false;
    const config = requested.presentation?.inside;
    const states = view!.viewer.getStates();
    const covers = new Set(config?.hide ? resolveParts(config.hide, Object.keys(states)) : []);
    const visible = Object.keys(states).filter(id => states[id].includes(1));
    insideAvailable = !!config && visible.some(id => !covers.has(id))
      && (!!config.section || visible.some(id => covers.has(id)));
  };
  const syncControls = () => {
    if (!view) return;
    const exploded = view.viewer.state.get("animationMode") === "explode";
    const grid = view.viewer.getGrids().some(Boolean);
    control("explode").setAttribute("aria-pressed", String(exploded));
    control("grid").setAttribute("aria-pressed", String(grid));
    control("inside").hidden = !insideAvailable;
    control("inside").setAttribute("aria-pressed", String(insideShown));
    const insideLabel = insideShown ? "Show outside" : "Show inside";
    control("inside").setAttribute("aria-label", insideLabel); control("inside").title = insideLabel;
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
    const fullscreen = transport.displayMode?.() === "fullscreen";
    const other = q(".aad-frame").offsetHeight - q(".aad-stage").offsetHeight;
    q(".aad-stage").style.height = `${fullscreen ? Math.max(240, innerHeight - other - 16) : STAGE_HEIGHT}px`;
    const bounds = q(".aad-stage").getBoundingClientRect(); view?.resize(bounds.width, bounds.height);
    if (!fullscreen) transport.requestSize(Math.ceil(document.documentElement.getBoundingClientRect().height));
    syncControls();
  };
  const applyRequest = (env: Envelope) => {
    if (!view) return;
    requested = structuredClone(env.viewer ?? {});
    view.batch(() => {
      applyPresentation(view!, requested.presentation);
      if (requested.view) view!.viewer.presetCamera(requested.view);
    });
    q(".aad-caption").textContent = requested.title || env.project;
    configureInside();
    syncControls();
  };
  control("explode").addEventListener("click", () => {
    if (view) view.batch(() => view!.setExploded(view!.viewer.state.get("animationMode") !== "explode"));
  });
  control("grid").addEventListener("click", () => { if (view) view.batch(() => view!.setGrid(!view!.viewer.getGrids().some(Boolean))); });
  control("inside").addEventListener("click", () => {
    const config = requested.presentation?.inside;
    if (!view || !config || !insideAvailable) return;
    view.batch(() => {
      const exploded = view!.viewer.state.get("animationMode") === "explode";
      view!.setExploded(false);
      const { inside: _inside, grid: _grid, explode: _explode, ...base } = requested.presentation!;
      if (!insideShown) {
        outsideCamera = view!.viewer.getCameraLocationSettings(); outsideExploded = exploded;
        applyPresentation(view!, { ...base, hide: config.hide ? [...(base.hide ?? []), ...config.hide] : base.hide,
          section: config.section ?? base.section, explode: false });
        insideShown = true;
        if (exploded && view!.canExplode()) view!.setExploded(true);
      } else {
        applyPresentation(view!, { ...base, explode: false });
        if (outsideCamera) view!.viewer.setCameraLocationSettings(outsideCamera.position, outsideCamera.quaternion, outsideCamera.target, outsideCamera.zoom);
        insideShown = false;
        if ((exploded || outsideExploded) && view!.canExplode()) view!.setExploded(true);
      }
    });
    syncControls();
  });
  control("reset").addEventListener("click", () => {
    if (!view) return;
    view.batch(() => {
      view!.reset();
      applyPresentation(view!, { ...requested?.presentation, explode: requested?.presentation?.explode ?? false, grid: requested?.presentation?.grid ?? false });
      view!.viewer.presetCamera(requested?.view ?? "iso");
    });
    configureInside();
    syncControls();
  });
  control("fullscreen").hidden = !transport.caps.fullscreen;
  control("fullscreen").addEventListener("click", () => void transport.requestFullscreen().then(size).catch(fail));

  const present = async (env: Envelope) => {
    if (!env.scene) throw new Error(env.summary || "The result has no model.");
    const nextKey = JSON.stringify({ presentation: env.viewer?.presentation ?? {}, view: env.viewer?.view, title: env.viewer?.title });
    if (env.scene.scene_id === diagnostics.shownScene) {
      if (requestKey !== nextKey) { applyRequest(env); requestKey = nextKey; }
      return;
    }
    diagnostics.state = "loading";
    const manifest = env.scene;
    if (manifest.format !== "tcv-5") throw new Error(`Unsupported scene format ${manifest.format}`);
    q<HTMLFieldSetElement>(".aad-toolbar").disabled = true;
    q(".aad-progress").hidden = false; q(".aad-error").hidden = true;
    const started = performance.now();
    const { bytes, stats } = await readScene(manifest.scene_id, manifest.total_bytes, transport.readChunk.bind(transport), (done, total) => {
      q<HTMLProgressElement>("progress").value = 100 * done / total;
    });
    const readAt = performance.now(); const text = await gunzipToText(bytes); const shapes = JSON.parse(text);
    const triangles = triangleCount(shapes);
    if (manifest.triangles !== undefined && triangles !== manifest.triangles) throw new Error(`Expected ${manifest.triangles} triangles, got ${triangles}`);
    const parsedAt = performance.now(); unsubscribe(); view?.dispose(); q(".aad-cad").replaceChildren();
    const bounds = q(".aad-stage").getBoundingClientRect();
    view = mountCad3d(q(".aad-cad"), shapes, { width: bounds.width, height: bounds.height, theme: resolvedTheme(transport), view: env.viewer?.view });
    (globalThis as { __viewer?: unknown }).__viewer = view.viewer;
    unsubscribe = view.viewer.state.subscribeAll(syncControls);
    applyRequest(env); requestKey = nextKey;
    diagnostics.shownScene = manifest.scene_id; diagnostics.triangles = triangles; diagnostics.verified = true;
    diagnostics.load = stats; diagnostics.rawBytes = text.length;
    diagnostics.ms = { read: readAt - started, parse: parsedAt - readAt, mount: performance.now() - parsedAt, sinceToolResult: performance.now() - started };
    diagnostics.loads++; diagnostics.state = "ready";
    q(".aad-progress").hidden = true; q<HTMLFieldSetElement>(".aad-toolbar").disabled = false;
    size();
  };
  const queue = (env: Envelope) => { loading = loading.then(() => present(env)).catch(fail); return loading; };
  transport.onUpdate(env => void queue(env)); new ResizeObserver(size).observe(q(".aad-frame"));
  transport.onThemeChange(() => { const theme = resolvedTheme(transport); q(".aad-frame").dataset.theme = theme; view?.setTheme(theme); size(); });
  addEventListener("resize", size);
  try { await queue(await transport.getInitialResult()); } catch (error) { fail(error); }
}
