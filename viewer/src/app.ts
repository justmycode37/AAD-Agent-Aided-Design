import { gunzipToText, readScene } from "./loader";
import type { CameraView, Envelope, SceneManifest, Transport } from "./transport/types";
import { mountCad3d, type Cad3dView } from "./viewer3d";
import type { DisplayMode } from "./display-mode";
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

/** Count original triangles without decoding or rewriting geometry buffers. */
export function triangleCount(scene: any): number {
  const visit = (node: any): number => {
    const shape = node.shape?.ref !== undefined ? scene.instances[node.shape.ref] : node.shape;
    const triangles = shape?.triangles;
    const count = Array.isArray(triangles) ? triangles.length / 3 : triangles?.buffer ? atob(triangles.buffer).length / 12 : 0;
    return count + (node.parts ?? []).reduce((total: number, part: any) => total + visit(part), 0);
  };
  return visit(scene.shapes ?? scene);
}

export async function runApp(transport: Transport, root: HTMLElement): Promise<void> {
  root.innerHTML = `
    <section class="aad-frame" aria-label="AAD CAD viewer">
      <fieldset class="aad-toolbar" aria-label="CAD viewing controls" disabled>
        <label>View <select aria-label="Standard view" data-control="view"><option value="iso">Isometric</option><option value="front">Front</option><option value="rear">Rear</option><option value="left">Left</option><option value="right">Right</option><option value="top">Top</option><option value="bottom">Bottom</option></select></label>
        <label>Display <select aria-label="Display style" data-control="style"><option value="shaded-edges">Shaded + edges</option><option value="shaded">Shaded</option><option value="wireframe">Wireframe</option><option value="transparent">Transparent</option></select></label>
        <button type="button" data-control="ortho" aria-pressed="false">Orthographic</button>
        <button type="button" data-control="section" aria-pressed="false">Section</button>
        <button type="button" data-control="explode" aria-pressed="false">Explode</button>
        <button type="button" data-control="measure" aria-pressed="false">Measure</button>
        <button type="button" data-control="parts" aria-expanded="false">Parts</button>
        <button type="button" data-control="reset">Reset view</button>
        <button type="button" data-control="fullscreen" hidden>Fullscreen</button>
      </fieldset>
      <div class="aad-part-controls" hidden>
        <label>Part <select aria-label="Part" data-control="part"></select></label>
        <button type="button" data-control="hide">Hide part</button><button type="button" data-control="isolate">Isolate part</button><button type="button" data-control="show-all">Show all</button>
      </div>
      <div class="aad-stage"><div class="aad-cad"></div><div class="aad-progress" role="status">Loading model… <progress max="100" value="0"></progress></div></div>
      <footer class="aad-footer"><span class="aad-title"></span><span class="aad-hint">Drag: rotate · Shift-drag / right-drag: pan · Scroll: zoom</span></footer>
      <div class="aad-notice" role="status" hidden></div>
      <div class="aad-error" role="alert" hidden></div>
    </section>`;
  const q = <T extends HTMLElement>(s: string) => root.querySelector(s) as T;
  const control = <T extends HTMLElement>(name: string) => q<T>(`[data-control="${name}"]`);
  q(".aad-frame").dataset.theme = resolvedTheme(transport);
  const fail = (error: unknown) => {
    diagnostics.state = "error";
    diagnostics.error = String((error as Error)?.stack ?? error);
    q(".aad-progress").hidden = true;
    q(".aad-error").hidden = false;
    q(".aad-error").textContent = `Could not show this model: ${(error as Error)?.message ?? error}`;
    console.error(error);
  };
  let view: Cad3dView | undefined;
  let unsubscribe = () => {};
  let loading: Promise<void> = Promise.resolve();
  const size = () => {
    const fullscreen = transport.displayMode?.() === "fullscreen";
    const frame = q(".aad-frame");
    const other = frame.offsetHeight - q(".aad-stage").offsetHeight;
    q(".aad-stage").style.height = `${fullscreen ? Math.max(240, window.innerHeight - other - 16) : STAGE_HEIGHT}px`;
    const bounds = q(".aad-stage").getBoundingClientRect();
    view?.resize(bounds.width, bounds.height);
    if (!fullscreen) transport.requestSize(Math.ceil(document.documentElement.getBoundingClientRect().height));
  };
  const notice = (text: string) => { q(".aad-notice").hidden = !text; q(".aad-notice").textContent = text; };
  const pressed = (name: string, flag: boolean) => control(name).setAttribute("aria-pressed", String(flag));
  const toggle = (name: string) => {
    const flag = control(name).getAttribute("aria-pressed") !== "true";
    pressed(name, flag);
    return flag;
  };
  control<HTMLSelectElement>("view").addEventListener("change", () => view?.viewer.presetCamera(control<HTMLSelectElement>("view").value as CameraView));
  control<HTMLSelectElement>("style").addEventListener("change", () => {
    const next = control<HTMLSelectElement>("style").value as DisplayMode;
    view?.setDisplayMode(next);
    control<HTMLSelectElement>("style").value = next;
  });
  control("ortho").addEventListener("click", () => view?.viewer.setOrtho(toggle("ortho")));
  control("section").addEventListener("click", () => {
    if (!view) return;
    if (toggle("section")) {
      view.viewer.setActiveTab("clip");
      view.viewer.setClipNormal(0, [-1, 0, 0], 0);
      notice("Section view: adjust the three clipping planes in the section panel. The original geometry is unchanged.");
    } else { view.viewer.resetClip(); view.viewer.setActiveTab("tree"); notice(""); }
  });
  control("explode").addEventListener("click", () => {
    if (!view) return;
    const enabled = toggle("explode");
    view.viewer.setExplode(enabled);
    if (enabled) { view.viewer.setRelativeTime(1); pressed("measure", false); notice(""); }
  });
  control("measure").addEventListener("click", () => {
    if (!view) return;
    const enabled = toggle("measure");
    if (enabled) { view.viewer.setExplode(false); pressed("explode", false); }
    view.viewer.activateTool("distance", enabled);
    notice(enabled ? "Mesh measurement: click two faces, edges, or vertices. Distances use the model’s units; this measures the tessellated display geometry." : "");
  });
  control("parts").addEventListener("click", () => {
    q(".aad-part-controls").hidden = !q(".aad-part-controls").hidden;
    control("parts").setAttribute("aria-expanded", String(!q(".aad-part-controls").hidden));
    size();
  });
  control("hide").addEventListener("click", () => view?.hidePart(control<HTMLSelectElement>("part").value));
  control("isolate").addEventListener("click", () => view?.isolatePart(control<HTMLSelectElement>("part").value));
  control("show-all").addEventListener("click", () => view?.showAll());
  control("reset").addEventListener("click", () => {
    view?.reset();
    for (const name of ["ortho", "section", "explode", "measure"]) pressed(name, false);
    control<HTMLSelectElement>("style").value = "shaded-edges";
    control<HTMLSelectElement>("view").value = "iso";
    notice("");
  });
  control("fullscreen").hidden = !transport.caps.fullscreen;
  control("fullscreen").addEventListener("click", () => void transport.requestFullscreen().catch(fail));

  const present = async (env: Envelope) => {
    if (!env.scene) throw new Error(env.summary || "The tool result has no scene.");
    if (env.scene.scene_id === diagnostics.shownScene) return;
    diagnostics.state = "loading";
    const manifest = env.scene as SceneManifest;
    if (manifest.format !== "tcv-5") throw new Error(`Scene format ${manifest.format} is not supported yet.`);
    q<HTMLFieldSetElement>(".aad-toolbar").disabled = true;
    q(".aad-progress").hidden = false;
    q(".aad-error").hidden = true;
    const started = performance.now();
    const { bytes, stats } = await readScene(manifest.scene_id, manifest.total_bytes, transport.readChunk.bind(transport), (done, total) => {
      q<HTMLProgressElement>("progress").value = 100 * done / total;
    });
    const readAt = performance.now();
    const text = await gunzipToText(bytes);
    const shapes = JSON.parse(text);
    const triangles = triangleCount(shapes);
    if (manifest.triangles !== undefined && triangles !== manifest.triangles) throw new Error(`Expected ${manifest.triangles} triangles, got ${triangles}`);
    const parsedAt = performance.now();
    unsubscribe();
    view?.dispose();
    q(".aad-cad").replaceChildren();
    const bounds = q(".aad-stage").getBoundingClientRect();
    view = mountCad3d(q(".aad-cad"), shapes, { width: bounds.width, height: bounds.height, theme: resolvedTheme(transport), view: env.viewer?.view });
    (globalThis as { __viewer?: unknown }).__viewer = view.viewer;
    unsubscribe = view.viewer.state.subscribeAll(() => {
      if (!view) return;
      const state = view.viewer.state;
      pressed("ortho", view.viewer.getOrtho());
      pressed("section", state.get("activeTab") === "clip");
      pressed("explode", state.get("animationMode") === "explode");
      pressed("measure", state.get("activeTool") === "distance");
      if (view.viewer.getTransparent()) control<HTMLSelectElement>("style").value = "transparent";
      else if (control<HTMLSelectElement>("style").value === "transparent") control<HTMLSelectElement>("style").value = "shaded-edges";
    });
    const part = control<HTMLSelectElement>("part");
    part.replaceChildren(...Object.keys(view.viewer.getStates()).map((path) => {
      const option = document.createElement("option"); option.value = path;
      option.textContent = manifest.parts.find((p) => p.path === path)?.name ?? path.split("/").at(-1) ?? path;
      return option;
    }));
    q(".aad-title").textContent = `${manifest.project} · rev ${manifest.rev} · ${triangles.toLocaleString()} triangles · ${manifest.units}`;
    q(".aad-title").title = `Original scene verified: ${manifest.scene_id}`;
    diagnostics.shownScene = manifest.scene_id; diagnostics.triangles = triangles; diagnostics.verified = true;
    diagnostics.load = stats; diagnostics.rawBytes = text.length;
    diagnostics.ms = { read: readAt - started, parse: parsedAt - readAt, mount: performance.now() - parsedAt, sinceToolResult: performance.now() - started };
    diagnostics.loads++; diagnostics.state = "ready";
    q(".aad-progress").hidden = true;
    q<HTMLFieldSetElement>(".aad-toolbar").disabled = false;
    size();
  };
  const queue = (env: Envelope) => { loading = loading.then(() => present(env)).catch(fail); return loading; };
  transport.onUpdate((env) => void queue(env));
  new ResizeObserver(size).observe(q(".aad-frame"));
  transport.onThemeChange(() => {
    const theme = resolvedTheme(transport);
    q(".aad-frame").dataset.theme = theme;
    view?.setTheme(theme);
    size();
  });
  window.addEventListener("resize", size);
  try { await queue(await transport.getInitialResult()); } catch (error) { fail(error); }
}
