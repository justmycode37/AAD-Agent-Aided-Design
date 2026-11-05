// In-process stand-in for the MCP server, reading a scene fixture from disk.
import { readFileSync } from "node:fs";
import { join } from "node:path";

export function loadFixture(dir) {
  const manifest = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));
  return { manifest, blob: readFileSync(join(dir, `${manifest.scene_id}.gz`)) };
}

export function fakeMcp({ manifest, blob }, { failFirstReadAt = null, extra = {} } = {}) {
  const stats = { chunks: 0, inFlight: 0, maxInFlight: 0, maxChunk: 0, failed: 0 };
  let failed = false;
  const toolResult = {
    content: [{ type: "text", text: `Showing rev ${manifest.rev}.` }],
    structuredContent: { ok: true, project: manifest.project, rev: manifest.rev, status: manifest.status, summary: `Showing rev ${manifest.rev}.`, scene: manifest, viewer: { mode: "inline" } },
  };
  const onRequest = async (method, params) => {
    if (method !== "tools/call") return {};
    const { name, arguments: a } = params;
    if (extra[name]) return extra[name](a);
    if (name !== "aad_scene_read") throw new Error(`Unknown tool ${name}`);
    stats.chunks++;
    stats.inFlight++;
    stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
    await new Promise((r) => setTimeout(r, 1));
    stats.inFlight--;
    if (failFirstReadAt === a.offset && !failed) {
      failed = true;
      stats.failed++;
      return { isError: true, content: [{ type: "text", text: "injected failure" }] };
    }
    const part = blob.subarray(a.offset, a.offset + a.length);
    stats.maxChunk = Math.max(stats.maxChunk, part.length);
    return {
      content: [{ type: "text", text: `${part.length} bytes` }],
      structuredContent: { data_b64: part.toString("base64"), offset: a.offset, byte_count: part.length, total_bytes: blob.length, has_more: a.offset + part.length < blob.length },
    };
  };
  return { toolResult, onRequest, stats };
}
