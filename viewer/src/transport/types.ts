// Types from CONTRACTS sections 6-8, limited to what a display-only viewer uses (PLAN D17).

export type Units = "mm" | "in";
export type Status = "failed" | "draft" | "verified" | "running";

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
  checks_summary?: { pass: number; fail: number; warn: number; skip: number };
  status: "draft" | "verified";
}

export type CameraView = "iso" | "front" | "rear" | "top" | "bottom" | "left" | "right";

export interface Presentation {
  isolate?: string[];
  hide?: string[];
  style?: "shaded-edges" | "shaded" | "wireframe" | "transparent";
  projection?: "orthographic" | "perspective";
  section?: { planes: { plane?: "XY" | "XZ" | "YZ"; normal?: [number, number, number]; offset?: number; flipped?: boolean }[]; intersection?: boolean };
  explode?: boolean;
  grid?: boolean;
}

export interface Envelope {
  ok: boolean;
  project: string;
  rev?: number;
  status: Status;
  summary: string;
  scene?: SceneManifest;
  viewer?: { mode: "inline" | "window" | "none"; url?: string; view?: CameraView; title?: string; presentation?: Presentation };
  [key: string]: unknown;
}

export interface Chunk {
  data_b64: string;
  offset: number;
  byte_count: number;
  total_bytes: number;
  has_more: boolean;
}

export interface TransportCaps {
  fullscreen: boolean;
}

export interface Transport {
  readonly caps: TransportCaps;
  getInitialResult(): Promise<Envelope>;
  readChunk(sceneId: string, offset: number, length: number): Promise<Chunk>;
  requestFullscreen(): Promise<void>;
  /** A later tool result, which may repeat the first: hosts can deliver one result twice. */
  onUpdate(cb: (e: Envelope) => void): void;
  /** Sized requests from the viewer, ignored by transports with no host frame. */
  requestSize(height: number): void;
  theme(): "light" | "dark" | undefined;
  onThemeChange(cb: () => void): void;
  displayMode?(): "inline" | "fullscreen";
}
