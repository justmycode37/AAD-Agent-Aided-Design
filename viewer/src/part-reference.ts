import type { PartReference, SceneManifest } from "./transport/types";

const mentionName = (name: string) => name.normalize("NFC").trim().replace(/[^\p{L}\p{N}_.-]+/gu, "_").slice(0, 80) || "part";

export function partReference(scene: SceneManifest, path: string): PartReference {
  const part = scene.parts.find(part => part.path === path);
  if (!part) throw new Error("This part is not in the displayed scene");
  const label = mentionName(part.name);
  const duplicates = scene.parts.filter(part => mentionName(part.name) === label).map(part => part.path).sort();
  return { project: scene.project, rev: scene.rev, scene_id: scene.scene_id, path, name: part.name,
    mention: `@${label}${duplicates.length > 1 ? `_${duplicates.indexOf(path) + 1}` : ""}` };
}

/** Carries the original revision and scene hash through hosts without metadata support. */
export function partReferenceUri(ref: PartReference): string {
  return `aad://part/${encodeURIComponent(ref.project)}/${ref.rev}/${ref.scene_id}?path=${encodeURIComponent(ref.path)}`;
}

/**
 * True only for hosts documenting that `ui/message` can leave content unsent.
 * Without this, `ui/message` sends a prompt, so it is never a fallback.
 */
export function supportsComposerReference(capabilities: unknown): boolean {
  const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
  return record(capabilities) && record(capabilities.experimental) && record(capabilities.experimental["openai/message"])
    && record(capabilities.message) && record(capabilities.message.text);
}

/** Appends a titled inline item to the current composer, without sending or opening a conversation. */
export function composerReferenceMessage(ref: PartReference) {
  return {
    role: "user" as const,
    content: [{ type: "text" as const, text: `AAD part reference: ${partReferenceUri(ref)}`, _meta: { "openai/title": ref.mention } }],
    _meta: { "openai/message": { target: "active" as const, send: false } },
  };
}

/** Leave room below hosts' context limits and state explicitly when it is partial. */
export function partSelectionContext(references: PartReference[], modalities: { text?: unknown; structuredContent?: unknown }) {
  const build = (included: PartReference[]) => {
    const omitted = references.length - included.length;
    const text = references.length
      ? `Selected AAD parts (display references, not files; ${included.length} of ${references.length} shared):\n${included.map(ref => `${ref.mention}: ${partReferenceUri(ref)}`).join("\n")}${omitted ? "\nSome references exceed the host context limit. Obtain their exact references before acting on those parts." : ""}`
      : "AAD part selection cleared.";
    return { ...(modalities.text ? { content: [{ type: "text" as const, text }] } : {}),
      ...(modalities.structuredContent ? { structuredContent: {
        selection: included.map(ref => ({ project: ref.project, rev: ref.rev, kind: "part", selector: partReferenceUri(ref), geometry: { type: "part" } })),
        selectedCount: references.length, omittedCount: omitted,
      } } : {}) };
  };
  const included: PartReference[] = [];
  for (const ref of references) {
    if (new TextEncoder().encode(JSON.stringify(build([...included, ref]))).byteLength > 12_000) break;
    included.push(ref);
  }
  return build(included);
}
