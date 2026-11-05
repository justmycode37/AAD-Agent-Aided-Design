import { gzipSync } from "node:zlib";
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { CHUNK_BYTES, MAX_IN_FLIGHT, gunzipToText, readScene } from "../src/loader";
import { sha256Js } from "../src/sha256";
import type { Chunk } from "../src/transport/types";

function serve(blob: Uint8Array, faults: (offset: number, attempt: number) => string | null = () => null) {
  const attempts = new Map<number, number>();
  return async (_id: string, offset: number, length: number): Promise<Chunk> => {
    const attempt = (attempts.get(offset) ?? 0) + 1;
    attempts.set(offset, attempt);
    const fault = faults(offset, attempt);
    if (fault === "throw") throw new Error("host error");
    const part = blob.subarray(offset, offset + length);
    if (fault === "short") {
      return { data_b64: Buffer.from(part.subarray(1)).toString("base64"), offset, byte_count: part.length - 1, total_bytes: blob.length, has_more: true };
    }
    await new Promise((r) => setTimeout(r, 1));
    return { data_b64: Buffer.from(part).toString("base64"), offset, byte_count: part.length, total_bytes: blob.length, has_more: offset + length < blob.length };
  };
}

function noise(size: number): Uint8Array {
  const out = new Uint8Array(size);
  let x = 12345;
  for (let i = 0; i < size; i++) {
    x = (x * 1103515245 + 12345) & 0x7fffffff;
    out[i] = x >> 16;
  }
  return out;
}

const sha = (b: Uint8Array) => createHash("sha256").update(b).digest("hex");

describe("readScene", () => {
  it("reassembles a large scene with at most 4 calls in flight", async () => {
    const blob = noise(CHUNK_BYTES * 9 + 17);
    const { bytes, stats } = await readScene(sha(blob), blob.length, serve(blob));
    expect(Buffer.compare(bytes, blob)).toBe(0);
    expect(stats.calls).toBe(10);
    expect(stats.maxInFlight).toBeLessThanOrEqual(MAX_IN_FLIGHT);
    expect(stats.maxInFlight).toBeGreaterThan(1);
  });

  it("retries failed and short chunks", async () => {
    const blob = noise(CHUNK_BYTES * 3);
    const reader = serve(blob, (offset, attempt) => {
      if (offset === CHUNK_BYTES && attempt === 1) return "throw";
      if (offset === CHUNK_BYTES * 2 && attempt <= 2) return "short";
      return null;
    });
    const { bytes, stats } = await readScene(sha(blob), blob.length, reader, undefined, 1);
    expect(Buffer.compare(bytes, blob)).toBe(0);
    expect(stats.retries).toBe(3);
  });

  it("gives up after repeated failures", async () => {
    const blob = noise(1000);
    await expect(readScene(sha(blob), blob.length, serve(blob, () => "throw"), undefined, 1)).rejects.toThrow("host error");
  });

  it("rejects a scene whose hash does not match scene_id", async () => {
    const blob = noise(1000);
    await expect(readScene("0".repeat(64), blob.length, serve(blob))).rejects.toThrow("does not match scene_id");
  });

  it("reports progress up to the total", async () => {
    const blob = noise(CHUNK_BYTES + 5);
    const seen: number[] = [];
    await readScene(sha(blob), blob.length, serve(blob), (done) => seen.push(done));
    expect(seen.at(-1)).toBe(blob.length);
  });
});

describe("gunzipToText", () => {
  it("round-trips gzip with the native stream", async () => {
    const text = JSON.stringify({ hello: "world", n: [1, 2, 3] });
    expect(await gunzipToText(gzipSync(text))).toBe(text);
  });
});

describe("sha256Js", () => {
  it.each([0, 1, 55, 56, 63, 64, 65, 1000, 100_000])("matches node for %i bytes", (n) => {
    const b = noise(n);
    expect(sha256Js(b)).toBe(sha(b));
  });
});
