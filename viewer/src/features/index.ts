// Viewer features register here. Each lives in its own file, so parallel work rarely touches the same lines.
import type { Envelope, SceneManifest, Transport } from "../transport/types";
import type { Cad3dView } from "../viewer3d";

export interface FeatureContext {
  transport: Transport;
  envelope: Envelope;
  scene: SceneManifest;
  /** Positioned container; features add their overlays here. */
  frame: HTMLElement;
  view: Cad3dView;
}

export type Feature = (ctx: FeatureContext) => void;

const features: Feature[] = [];

export function startFeatures(ctx: FeatureContext): void {
  for (const feature of features) feature(ctx);
}
