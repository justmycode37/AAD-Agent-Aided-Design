// Chunk loader from CONTRACTS section 7: 128 KiB of compressed bytes per call, at most 4 in flight,
// each chunk retried, and the result checked against total_bytes and scene_id.
import { sha256Hex } from "./sha256";
import type { Chunk } from "./transport/types";

export const CHUNK_BYTES = 128 * 1024;
export const MAX_IN_FLIGHT = 4;
const MAX_ATTEMPTS = 4;

export type ChunkReader = (sceneId: string, offset: number, length: number) => Promise<Chunk>;

export interface LoadStats {
  calls: number;
  retries: number;
  maxInFlight: number;
  bytes: number;
}

export function decodeBase64(text: string): Uint8Array {
  const native = (Uint8Array as unknown as { fromBase64?: (s: string) => Uint8Array }).fromBase64;
  if (native) return native(text);
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function encodeBase64(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export async function readScene(
  sceneId: string,
  totalBytes: number,
  read: ChunkReader,
  onProgress: (done: number, total: number) => void = () => {},
  backoffMs = 100,
): Promise<{ bytes: Uint8Array; stats: LoadStats }> {
  const out = new Uint8Array(totalBytes);
  const stats: LoadStats = { calls: 0, retries: 0, maxInFlight: 0, bytes: totalBytes };
  let next = 0;
  let done = 0;
  let inFlight = 0;

  const fetchChunk = async (offset: number, length: number) => {
    for (let attempt = 1; ; attempt++) {
      inFlight++;
      stats.calls++;
      stats.maxInFlight = Math.max(stats.maxInFlight, inFlight);
      try {
        const chunk = await read(sceneId, offset, length);
        if (chunk.offset !== offset) throw new Error(`Chunk offset ${chunk.offset}, asked for ${offset}`);
        if (chunk.total_bytes !== totalBytes) {
          throw new Error(`Chunk says ${chunk.total_bytes} total bytes, manifest says ${totalBytes}`);
        }
        const data = decodeBase64(chunk.data_b64);
        if (data.length !== length || chunk.byte_count !== length) {
          throw new Error(`Chunk at ${offset} has ${data.length} bytes, asked for ${length}`);
        }
        out.set(data, offset);
        done += length;
        onProgress(done, totalBytes);
        return;
      } catch (error) {
        if (attempt >= MAX_ATTEMPTS) throw error;
        stats.retries++;
        await new Promise((r) => setTimeout(r, backoffMs * 2 ** (attempt - 1)));
      } finally {
        inFlight--;
      }
    }
  };

  const lane = async () => {
    while (next < totalBytes) {
      const offset = next;
      const length = Math.min(CHUNK_BYTES, totalBytes - offset);
      next += length;
      await fetchChunk(offset, length);
    }
  };
  await Promise.all(Array.from({ length: MAX_IN_FLIGHT }, lane));

  const hash = await sha256Hex(out);
  if (hash !== sceneId) throw new Error(`Scene hash ${hash} does not match scene_id ${sceneId}`);
  return { bytes: out, stats };
}

export async function gunzipToText(bytes: Uint8Array): Promise<string> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).text();
}
