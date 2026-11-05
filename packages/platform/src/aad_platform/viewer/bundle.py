"""Lossless scene assets for HTML hosts that inline local images.

MCP Apps use aad_scene_read instead. This export keeps large scene bytes out of
the HTML tool argument, without changing any mesh buffers or scene metadata.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from . import viewer_path


def _asset(path: Path, payload: bytes, element_id: str, encoding: str = "base64+gzip") -> Path:
    encoded = base64.b64encode(payload).decode("ascii")
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
        f'<metadata id="{element_id}" encoding="{encoding}">{encoded}</metadata></svg>',
        encoding="utf-8",
    )
    return path.resolve()


def _script_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True).replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")


def export_bundle(scene_dir: Path, output_dir: Path) -> Path:
    """Write viewer.html and a lossless SVG metadata asset for one saved scene.

    Pass viewer.html's text to an HTML host with local image inlining support.
    The host must bundle the referenced asset; opening the source HTML directly
    reports that requirement instead of silently substituting geometry.
    """
    manifest = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    payload = (scene_dir / "scene.bin.gz").read_bytes()
    if len(payload) != manifest["total_bytes"]:
        raise ValueError("Scene size does not match the manifest")
    if hashlib.sha256(payload).hexdigest() != manifest["scene_id"]:
        raise ValueError("Scene hash does not match the manifest")

    output_dir.mkdir(parents=True, exist_ok=True)
    asset = _asset(output_dir / f"scene-{manifest['scene_id']}.svg", payload, "aad-scene")
    # Keep the complete manifest, including part names and the original scene ID.
    config = _script_json({"manifest": manifest, "asset": str(asset)})
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
</head><body><main id="aad-boot" style="min-height:560px" role="status">Loading CAD viewer…</main>
<script>
try {
  const asset = RUNTIME;
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
    document.body.append(script);
  }
} catch (error) { document.body.textContent = "Could not load CAD viewer: " + error.message; }
</script></body></html>'''
    html_bundle = bootstrap.replace("CONFIG", config, 1).replace("RUNTIME", _script_json(str(runtime_asset)), 1)
    # A separate, genuinely standalone file also opens with no chat host/server.
    standalone_config = _script_json({"manifest": manifest, "asset": "data:image/svg+xml;base64," + base64.b64encode(asset.read_bytes()).decode("ascii")})
    standalone = html.replace("<head>", f"<head>\n<script>window.__AAD_EMBEDDED_SCENE__ = {standalone_config};</script>", 1)
    (output_dir / "standalone.html").write_text(standalone, encoding="utf-8")
    target = output_dir / "viewer.html"
    target.write_text(html_bundle, encoding="utf-8")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(export_bundle(args.scene_dir, args.output_dir))
