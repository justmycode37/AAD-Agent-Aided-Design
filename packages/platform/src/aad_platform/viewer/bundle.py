"""Lossless scene assets for HTML hosts that inline local images.

MCP Apps use aad_scene_read instead. This export keeps large scene bytes out of
the HTML tool argument, without changing any mesh buffers or scene metadata.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path

from . import viewer_path
from ..presentation import VIEWS, normalize_presentation, normalize_views

HTML_ARGUMENT_LIMIT = 512_000
HTML_INLINE_LIMIT = 25 * 1024 * 1024


def _asset(path: Path, payload: bytes, element_id: str, encoding: str = "base64+gzip") -> Path:
    encoded = base64.b64encode(payload).decode("ascii")
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
        f'<metadata id="{element_id}" encoding="{encoding}">{encoded}</metadata></svg>',
        encoding="utf-8",
    )
    return path.resolve()


def _png_asset(path: Path, payload: bytes, width: int = 2048) -> Path:
    """Store arbitrary bytes losslessly in RGB pixels without nested base64."""
    height = (len(payload) + width * 3 - 1) // (width * 3)
    padded = payload + bytes(width * height * 3 - len(payload))
    rows = b"".join(b"\0" + padded[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, level=1)) + chunk(b"IEND", b""))
    path.write_bytes(png)
    return path.resolve()


def _script_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True).replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")


def export_bundle(scene_dir: Path, output_dir: Path, *, presentation: dict | None = None, view: str | None = None, title: str | None = None, views: list | None = None, controls: str = "simple") -> Path:
    """Write chat and standalone viewers for one saved scene.

    Pass viewer.html's text to an HTML host with local image inlining support.
    The scene is carried in lossless PNG chunks and the viewer runtime in an SVG
    asset. Opening viewer.html directly reports missing host-inlined assets
    instead of silently substituting geometry. standalone.html works offline.
    """
    manifest = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    payload = (scene_dir / "scene.bin.gz").read_bytes()
    if len(payload) != manifest["total_bytes"]:
        raise ValueError("Scene size does not match the manifest")
    if hashlib.sha256(payload).hexdigest() != manifest["scene_id"]:
        raise ValueError("Scene hash does not match the manifest")
    settings = {}
    if controls not in ("simple", "cad"):
        raise ValueError("controls must be simple or cad")
    if views is not None:
        settings["views"] = normalize_views(views, manifest.get("parts", []))
    if presentation is not None:
        settings["presentation"] = normalize_presentation(presentation, manifest.get("parts", []))
    if view is not None:
        if view not in VIEWS:
            raise ValueError("Unknown camera view")
        settings["view"] = view
    if title is not None:
        if not isinstance(title, str) or len(title) > 160:
            raise ValueError("title must be a string of at most 160 characters")
        settings["title"] = title

    output_dir.mkdir(parents=True, exist_ok=True)
    chunk_size = 4 * 1024 * 1024
    chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
    assets = [_png_asset(output_dir / f"scene-{manifest['scene_id']}-{i}.png", chunk) for i, chunk in enumerate(chunks)]
    # Keep the complete manifest, including part names and the original scene ID.
    # Keep file paths only in <img src>. Duplicating them in JSON makes some
    # hosts inline each large image a second time when rewriting local assets.
    config = _script_json({"manifest": manifest, "chunk_bytes": [len(c) for c in chunks], **settings})
    html = viewer_path().read_text(encoding="utf-8")
    # Bundle the runtime separately too: the full CAD library is larger than
    # HTML-only hosts' tool-argument limit. Its scripts remain ordinary inline
    # scripts after unpacking; no eval, external URL, or dynamic code compiler.
    runtime = html.encode("utf-8")
    runtime_id = hashlib.sha256(runtime).hexdigest()
    runtime_asset = _asset(output_dir / f"viewer-{runtime_id}.svg", runtime, "aad-runtime", "base64+utf8")
    bootstrap = r'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<script>window.__AAD_EMBEDDED_SCENE__ = CONFIG;</script>
<div hidden id="aad-assets">ASSET_TAGS</div>
<style>
#aad-boot { min-height:510px; }
.aad-boot-stage { position:relative; height:480px; }
.aad-boot-bar { position:absolute; top:50%; left:50%; width:180px; max-width:60%; height:2px; overflow:hidden; border-radius:2px; transform:translate(-50%,-50%); background:color-mix(in srgb,var(--foreground,#8d96a2) 18%,transparent); }
.aad-boot-bar span { display:block; width:35%; height:100%; background:var(--foreground,#8d96a2); opacity:.7; animation:aad-boot-loading 1.2s ease-in-out infinite; }
@keyframes aad-boot-loading { from { transform:translateX(-110%); } to { transform:translateX(390%); } }
@media (prefers-reduced-motion:reduce) { .aad-boot-bar span { animation:none; transform:translateX(90%); } }
</style>
</head><body><main id="aad-boot"><div class="aad-boot-stage"><div class="aad-boot-bar" role="progressbar" aria-label="Loading viewer"><span></span></div></div></main>
<script type="module">
await (async () => {
await new Promise(resolve => { setTimeout(resolve, 80); requestAnimationFrame(() => requestAnimationFrame(resolve)); });
try {
  const bundle = window.__AAD_EMBEDDED_SCENE__;
  bundle.assets = [...document.querySelectorAll("#aad-assets img[data-aad-chunk]")].map(img => img.src);
  const asset = document.querySelector("#aad-runtime-asset")?.src;
  if (!asset) throw new Error("Viewer runtime asset is missing");
  if (!asset.startsWith("data:image/svg+xml;base64,")) throw new Error("Bundle the viewer asset before displaying this HTML");
  const decode = (text) => Uint8Array.from(atob(text), c => c.charCodeAt(0));
  const xml = new DOMParser().parseFromString(new TextDecoder().decode(decode(asset.split(",")[1])), "image/svg+xml");
  const payload = xml.getElementById("aad-runtime");
  if (!payload) throw new Error("Viewer asset has no AAD runtime");
  const html = new TextDecoder().decode(decode(payload.textContent.trim()));
  const app = new DOMParser().parseFromString(html, "text/html");
  for (const style of app.querySelectorAll("style")) document.head.append(style.cloneNode(true));
  const scripts = [...app.querySelectorAll("script")];
  for (const script of scripts) script.remove();
  const root = document.getElementById("aad-boot");
  root.removeAttribute("role");
  root.replaceChildren(...app.body.childNodes);
  for (const source of scripts) {
    if (source.src) throw new Error("The AAD viewer must contain only inline scripts");
    const script = document.createElement("script");
    if (source.type) script.type = source.type;
    script.textContent = source.textContent;
    await new Promise((resolve, reject) => {
      script.addEventListener("load", resolve, { once: true });
      script.addEventListener("error", () => reject(new Error("Could not start the CAD viewer runtime")), { once: true });
      document.body.append(script);
    });
  }
} catch (error) { document.body.textContent = "Could not load CAD viewer: " + error.message; }
})();
</script></body></html>'''
    tags = "".join(f'<img src="{p}" data-aad-chunk="{i}">' for i, p in enumerate(assets))
    tags += f'<img id="aad-runtime-asset" src="{runtime_asset}">'
    html_bundle = bootstrap.replace("CONFIG", config, 1).replace("ASSET_TAGS", tags, 1)
    # A separate, genuinely standalone file also opens with no chat host/server.
    standalone_config = _script_json({"manifest": manifest, "assets": ["data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii") for p in assets], "chunk_bytes": [len(c) for c in chunks], "controls": controls, **settings})
    standalone = html.replace("<head>", f"<head>\n<script>window.__AAD_EMBEDDED_SCENE__ = {standalone_config};</script>", 1)
    (output_dir / "standalone.html").write_text(standalone, encoding="utf-8")
    # Refuse before writing a file that cannot fit T3's tool argument limit.
    argument_chars = len(html_bundle.encode("utf-16-le")) // 2
    if argument_chars > HTML_ARGUMENT_LIMIT:
        raise ValueError(
            f"viewer.html is {argument_chars:,} characters, above the 512,000-character html_preview/html_render argument limit; "
            f"standalone.html was written to {output_dir / 'standalone.html'}. Use it locally or split the scene/part listing."
        )
    # T3 currently allows 25 MiB after local image inlining. Estimate one
    # base64 expansion for each PNG plus the bundled viewer SVG; this is host-specific.
    inline_estimate = len(html_bundle.encode("utf-8")) + sum(p.stat().st_size for p in assets + [runtime_asset]) * 4 // 3 + 4096
    if inline_estimate > HTML_INLINE_LIMIT:
        raise ValueError(
            f"Estimated inlined viewer size {inline_estimate / 1048576:.2f} MiB exceeds the host-specific 25 MiB html_preview limit; "
            f"standalone.html was written to {output_dir / 'standalone.html'}. Open that file locally or use a host with a larger limit."
        )
    target = output_dir / "viewer.html"
    target.write_text(html_bundle, encoding="utf-8")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--presentation", type=json.loads, help="Agent-controlled view settings as JSON")
    parser.add_argument("--view", choices=VIEWS)
    parser.add_argument("--title")
    parser.add_argument("--views", type=json.loads, help="Named user-selectable view recipes as JSON")
    parser.add_argument("--controls", choices=("simple", "cad"), default="simple", help="Standalone controls; chat exports always remain simple")
    args = parser.parse_args()
    print(export_bundle(args.scene_dir, args.output_dir, presentation=args.presentation, view=args.view, title=args.title, views=args.views, controls=args.controls))
