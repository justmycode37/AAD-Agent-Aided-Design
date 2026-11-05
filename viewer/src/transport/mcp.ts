// MCP Apps transport: postMessage to the host through @modelcontextprotocol/ext-apps.
import { App } from "@modelcontextprotocol/ext-apps";
import type { Chunk, Envelope, Transport, TransportCaps } from "./types";

const APP_INFO = { name: "aad-viewer", version: "0.0.1" };

function structured<T>(name: string, result: { isError?: boolean; structuredContent?: unknown; content?: unknown }): T {
  if (result.isError) {
    const text = Array.isArray(result.content)
      ? result.content.map((c: { text?: string }) => c.text ?? "").join(" ")
      : "";
    throw new Error(`${name} failed: ${text || "unknown error"}`);
  }
  if (result.structuredContent === undefined) throw new Error(`${name} returned no structured content`);
  return result.structuredContent as T;
}

export class McpTransport implements Transport {
  caps: TransportCaps = { fullscreen: false };
  private readonly app: App;
  private initial: Promise<Envelope>;
  private resolveInitial!: (e: Envelope) => void;
  private updateCb: (e: Envelope) => void = () => {};
  private themeCb: () => void = () => {};
  private haveInitial = false;

  constructor() {
    this.app = new App(APP_INFO, { availableDisplayModes: ["inline", "fullscreen"] }, { autoResize: false });
    this.initial = new Promise((resolve) => (this.resolveInitial = resolve));
    // Handlers must exist before connect(), because the host sends the result right after init.
    this.app.ontoolresult = (result) => {
      const env = result.structuredContent as Envelope | undefined;
      if (!env) return;
      if (this.haveInitial) {
        this.updateCb(env);
      } else {
        this.haveInitial = true;
        this.resolveInitial(env);
      }
    };
    this.app.onhostcontextchanged = () => this.themeCb();
  }

  async connect(): Promise<void> {
    await this.app.connect();
    const modes = this.app.getHostContext()?.availableDisplayModes ?? [];
    this.caps = { fullscreen: modes.includes("fullscreen") };
  }

  getInitialResult(): Promise<Envelope> {
    return this.initial;
  }

  async readChunk(sceneId: string, offset: number, length: number): Promise<Chunk> {
    const result = await this.app.callServerTool({
      name: "aad_scene_read",
      arguments: { scene_id: sceneId, offset, length },
    });
    return structured<Chunk>("aad_scene_read", result);
  }

  async requestFullscreen(): Promise<void> {
    const current = this.app.getHostContext()?.displayMode;
    await this.app.requestDisplayMode({ mode: current === "fullscreen" ? "inline" : "fullscreen" });
  }

  onUpdate(cb: (e: Envelope) => void): void {
    this.updateCb = cb;
  }

  requestSize(height: number): void {
    if (this.app.getHostContext()?.displayMode !== "fullscreen") void this.app.sendSizeChanged({ height });
  }

  theme(): "light" | "dark" | undefined {
    return this.app.getHostContext()?.theme;
  }

  onThemeChange(cb: () => void): void {
    this.themeCb = cb;
  }

  displayMode(): "inline" | "fullscreen" {
    return this.app.getHostContext()?.displayMode === "fullscreen" ? "fullscreen" : "inline";
  }
}
