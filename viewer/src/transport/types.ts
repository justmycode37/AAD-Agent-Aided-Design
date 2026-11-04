// Types from CONTRACTS sections 6-8. Keep in step with that file.

export type Units = "mm" | "in";
export type Status = "failed" | "draft" | "verified" | "running";

export interface SceneParam {
  name: string;
  value: number | string;
  min?: number;
  max?: number;
  unit?: string;
}

export interface SceneManifest {
  scene_id: string;
  project: string;
  rev: number;
  kind: "3d" | "2d";
  format: "tcv-5" | "svg-1";
  encoding: "gzip";
  units: Units;
  bbox: [number, number, number, number, number, number];
  total_bytes: number;
  triangles?: number;
  parts: { path: string; name: string; feature?: string; color?: string }[];
  params?: SceneParam[];
  checks_summary?: { pass: number; fail: number; warn: number; skip: number };
  failed_features?: string[];
  status: "draft" | "verified";
  compare?: SceneManifest;
}

export interface Envelope {
  ok: boolean;
  project: string;
  rev?: number;
  status: Status;
  summary: string;
  scene?: SceneManifest;
  viewer?: { mode: "inline" | "window" | "none"; url?: string };
  [key: string]: unknown;
}

export interface Chunk {
  data_b64: string;
  offset: number;
  byte_count: number;
  total_bytes: number;
  has_more: boolean;
}

export interface Selection {
  project: string;
  rev: number;
  kind: "face" | "edge" | "vertex" | "part";
  selector: string;
  feature?: string;
  geometry: {
    type: string;
    center?: number[];
    normal?: number[];
    radius?: number;
    area?: number;
    length?: number;
  };
}

/** What the host can do, so the UI can hide controls for missing features. */
export interface TransportCaps {
  sendMessage: boolean;
  setModelContext: boolean;
  fullscreen: boolean;
  callTool: boolean;
  download: boolean;
}

export interface Transport {
  readonly caps: TransportCaps;
  getInitialResult(): Promise<Envelope>;
  readChunk(sceneId: string, offset: number, length: number): Promise<Chunk>;
  callTool(name: string, args: object): Promise<Envelope>;
  setModelContext(ctx: object): Promise<void>;
  sendMessage(text: string): Promise<void>;
  requestFullscreen(): Promise<void>;
  onUpdate(cb: (e: Envelope) => void): void;
  /** Sized requests from the viewer, ignored by transports with no host frame. */
  requestSize(height: number): void;
  theme(): "light" | "dark" | undefined;
  onThemeChange(cb: () => void): void;
}
