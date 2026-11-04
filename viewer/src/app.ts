// The one UI, written against Transport so all three transports share it.
import { gunzipToText, readScene } from "./loader";
import { startFeatures } from "./features";
import type { SceneManifest, Transport } from "./transport/types";
import { mountCad3d, type Cad3dView } from "./viewer3d";

const INLINE_HEIGHT = 560;

type Diagnostics = {
  state: "boot" | "loading" | "ready" | "error";
  ms?: Record<string, number>;
  load?: unknown;
  rawBytes?: number;
  error?: string;
  host?: unknown;
};

/** For tests and host diagnosis. Contains no scene data. */
export const diagnostics: Diagnostics = { state: "boot" };
(globalThis as { __aiad?: Diagnostics }).__aiad = diagnostics;

function resolvedTheme(transport: Transport): "light" | "dark" {
  return transport.theme() ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

export async function runApp(transport: Transport, root: HTMLElement): Promise<void> {
  root.innerHTML = `
    <div class="aiad-frame" style="position:relative;width:100vw;height:100vh;min-height:240px;overflow:hidden">
      <div class="aiad-cad" style="position:absolute;inset:0"></div>
      <div class="aiad-progress" style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:min(60%,320px);text-align:center;color:#778">
        <div class="aiad-progress-label">Loading part</div>
        <div style="height:4px;background:#8884;border-radius:2px;overflow:hidden;margin-top:6px">
          <div class="aiad-progress-fill" style="height:100%;width:0;background:#3a7bd5"></div>
        </div>
      </div>
      <div class="aiad-error" style="position:absolute;inset:auto 8px 8px 8px;color:#c33;white-space:pre-wrap"></div>
    </div>`;
  const q = <T extends HTMLElement>(s: string) => root.querySelector(s) as T;
  const fail = (error: unknown) => {
    diagnostics.state = "error";
    diagnostics.error = String((error as Error)?.stack ?? error);
    q(".aiad-progress").style.display = "none";
    q(".aiad-error").textContent = `The viewer could not show this part: ${(error as Error)?.message ?? error}`;
    console.error(error);
  };
  addEventListener("error", (e) => fail(e.error ?? e.message));
  addEventListener("unhandledrejection", (e) => fail(e.reason));

  transport.requestSize(INLINE_HEIGHT);
  try {
    const env = await transport.getInitialResult();
    const started = performance.now();
    if (!env.scene) throw new Error(env.summary || "The tool result has no scene.");
    const view = await show(transport, env.scene, q(".aiad-frame"), q(".aiad-cad"), (done, total) => {
      q(".aiad-progress-fill").style.width = `${((100 * done) / total).toFixed(1)}%`;
    });
    q(".aiad-progress").style.display = "none";
    new ResizeObserver(() => {
      const r = q(".aiad-frame").getBoundingClientRect();
      view.resize(r.width, r.height);
    }).observe(q(".aiad-frame"));
    transport.onThemeChange(() => view.setTheme(resolvedTheme(transport)));
    startFeatures({ transport, envelope: env, scene: env.scene, frame: q(".aiad-frame"), view });
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    diagnostics.ms = { ...diagnostics.ms, sinceToolResult: performance.now() - started };
    diagnostics.state = "ready";
  } catch (error) {
    fail(error);
  }
}

async function show(
  transport: Transport,
  scene: SceneManifest,
  frame: HTMLElement,
  host: HTMLElement,
  onProgress: (done: number, total: number) => void,
): Promise<Cad3dView> {
  diagnostics.state = "loading";
  if (scene.format !== "tcv-5") throw new Error(`Scene format ${scene.format} is not supported yet.`);
  const t0 = performance.now();
  const { bytes, stats } = await readScene(scene.scene_id, scene.total_bytes, transport.readChunk.bind(transport), onProgress);
  const t1 = performance.now();
  const text = await gunzipToText(bytes);
  const shapes = JSON.parse(text);
  const t2 = performance.now();
  diagnostics.load = stats;
  diagnostics.rawBytes = text.length;
  const r = frame.getBoundingClientRect();
  const view = mountCad3d(host, shapes, { width: r.width, height: r.height, theme: resolvedTheme(transport) });
  (globalThis as { __viewer?: unknown }).__viewer = view.viewer;
  diagnostics.ms = { read: t1 - t0, parse: t2 - t1, mount: performance.now() - t2 };
  return view;
}
