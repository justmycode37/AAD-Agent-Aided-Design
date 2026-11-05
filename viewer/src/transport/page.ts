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
  requestSize(): void {}
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
  asset: string;
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
  constructor(bundle: BundledScene) {
    super({ scene_b64: "", envelope: {
      ok: true, project: bundle.manifest.project, rev: bundle.manifest.rev,
      status: bundle.manifest.status, summary: "Saved scene", scene: bundle.manifest,
      viewer: { mode: "inline", presentation: bundle.presentation, view: bundle.view, title: bundle.title, views: bundle.views },
    } }, decodeSceneAsset(bundle.asset));
    this.controls = bundle.controls ?? "simple";
  }
}
