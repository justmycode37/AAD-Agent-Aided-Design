// Transports for a page outside any host: the opt-in local URL, and the standalone viewer_html export.
import { decodeBase64, encodeBase64 } from "../loader";
import type { Chunk, Envelope, SceneManifest, Transport, TransportCaps } from "./types";

function browserFullscreen(): Promise<void> {
  return document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
}

abstract class PageTransport implements Transport {
  caps: TransportCaps = { fullscreen: document.fullscreenEnabled };
  abstract getInitialResult(): Promise<Envelope>;
  abstract readChunk(sceneId: string, offset: number, length: number): Promise<Chunk>;
  requestFullscreen = browserFullscreen;
  onUpdate(_cb: (e: Envelope) => void): void {}
  requestSize(height: number): void {
    // HTML-only chat hosts support the same size notification without an MCP
    // handshake. In a standalone window the document simply grows normally.
    if (parent !== window) parent.postMessage({ jsonrpc: "2.0", method: "ui/notifications/size-changed", params: { height } }, "*");
  }
  theme(): "light" | "dark" | undefined {
    return undefined;
  }
  onThemeChange(cb: () => void): void {
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", cb);
    document.addEventListener("fullscreenchange", cb);
  }
  displayMode(): "inline" | "fullscreen" {
    return document.fullscreenElement ? "fullscreen" : "inline";
  }
}

export interface WindowConfig {
  project: string;
  token: string;
}

/** Same-origin routes from CONTRACTS section 8. */
export class WindowTransport extends PageTransport {
  readonly controls = "cad";
  constructor(private readonly config: WindowConfig) {
    super();
    // Keep the token out of the address bar and history once the page has it.
    if (new URLSearchParams(location.search).has("token")) history.replaceState(null, "", location.pathname);
  }

  /** New revisions of this project, while the page stays open. */
  onUpdate(cb: (e: Envelope) => void): void {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const query = new URLSearchParams({ token: this.config.token, project: this.config.project });
    const socket = new WebSocket(`${scheme}//${location.host}/ws?${query}`);
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "result" && message.envelope?.scene) cb(message.envelope);
    };
  }

  private async rpc<T>(method: string, params: object): Promise<T> {
    const response = await fetch("/rpc", {
      method: "POST",
      headers: { Authorization: `Bearer ${this.config.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ method, params }),
    });
    const result = await response.json();
    if (!response.ok || result.isError) throw new Error(result?.structuredContent?.summary ?? `${method} failed (${response.status})`);
    return result.structuredContent as T;
  }

  getInitialResult(): Promise<Envelope> {
    return this.rpc<Envelope>("aad_show", { project: this.config.project });
  }

  readChunk(sceneId: string, offset: number, length: number): Promise<Chunk> {
    return this.rpc<Chunk>("aad_scene_read", { scene_id: sceneId, offset, length });
  }
}

export interface EmbeddedScene {
  envelope: Envelope;
  scene_b64: string;
}

export interface BundledScene {
  manifest: SceneManifest;
  assets?: string[];
  chunk_bytes?: number[];
  asset?: string;
  presentation?: import("./types").Presentation;
  view?: import("./types").CameraView;
  title?: string;
  views?: import("./types").NamedView[];
  controls?: "simple" | "cad";
}

/** Local-asset transport for HTML chat hosts; preserves the original gzip bytes. */
export function decodeSceneAsset(asset: string): Uint8Array {
  if (!asset.startsWith("data:image/svg+xml;base64,")) throw new Error("Bundle the scene asset before displaying this HTML");
  const svg = new DOMParser().parseFromString(new TextDecoder().decode(decodeBase64(asset.split(",")[1])), "image/svg+xml");
  const payload = svg.getElementById("aad-scene");
  if (!payload) throw new Error("Scene asset has no AAD payload");
  return decodeBase64(payload.textContent?.trim() ?? "");
}

export async function decodeSceneAssets(assets: string[], counts: number[], total: number): Promise<Uint8Array> {
  if (!assets.length || assets.length !== counts.length) throw new Error("Scene bundle is missing one or more chunks");
  const chunks = await Promise.all(assets.map(async (asset, i) => {
    if (!asset) throw new Error(`Scene chunk ${i} is missing`);
    if (!asset.startsWith("data:image/png;base64,")) throw new Error(`Scene chunk ${i} was not bundled by the host`);
    const image = new Image(); image.src = asset;
    try { await image.decode(); } catch { throw new Error(`Scene chunk ${i} is missing or invalid`); }
    const canvas = document.createElement("canvas"); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error(`Could not decode scene chunk ${i}`);
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    if (counts[i] > Math.floor(pixels.length / 4) * 3) throw new Error(`Scene chunk ${i} byte count exceeds its PNG payload`);
    const bytes = new Uint8Array(counts[i]);
    for (let j = 0; j < bytes.length; j++) bytes[j] = pixels[Math.floor(j / 3) * 4 + (j % 3)];
    if (bytes.length !== counts[i]) throw new Error(`Scene chunk ${i} byte count mismatch`);
    return bytes;
  }));
  const size = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  if (size !== total) throw new Error("Scene bundle byte count mismatch");
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return bytes;
}

/** The scene travels in the file, so the same loader checks its size and hash. */
export class EmbeddedTransport extends PageTransport {
  private readonly bytes: Uint8Array;

  constructor(private readonly embedded: EmbeddedScene, bytes?: Uint8Array) {
    super();
    this.bytes = bytes ?? decodeBase64(embedded.scene_b64);
  }

  async getInitialResult(): Promise<Envelope> {
    return this.embedded.envelope;
  }

  async readChunk(_sceneId: string, offset: number, length: number): Promise<Chunk> {
    const part = this.bytes.subarray(offset, offset + length);
    return { data_b64: encodeBase64(part), offset, byte_count: part.length, total_bytes: this.bytes.length, has_more: offset + part.length < this.bytes.length };
  }
}

export class BundledTransport extends EmbeddedTransport {
  readonly controls: "simple" | "cad";
  static async create(bundle: BundledScene): Promise<BundledTransport> {
    const bytes = bundle.assets ? await decodeSceneAssets(bundle.assets, bundle.chunk_bytes ?? [], bundle.manifest.total_bytes) : decodeSceneAsset(bundle.asset!);
    return new BundledTransport(bundle, bytes);
  }
  constructor(bundle: BundledScene, bytes: Uint8Array) {
    super({ scene_b64: "", envelope: {
      ok: true, project: bundle.manifest.project, rev: bundle.manifest.rev,
      status: bundle.manifest.status, summary: "Saved scene", scene: bundle.manifest,
      viewer: { mode: "inline", presentation: bundle.presentation, view: bundle.view, title: bundle.title, views: bundle.views },
    } }, bytes);
    this.controls = bundle.controls ?? "simple";
  }
}
