"""Lossless scene assets for HTML hosts that inline local images.

MCP Apps use aad_scene_read instead. This export keeps large scene bytes out of
the HTML tool argument, without changing any mesh buffers or scene metadata.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from . import STUB


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
    asset = (output_dir / f"scene-{manifest['scene_id']}.svg").resolve()
    encoded = base64.b64encode(payload).decode("ascii")
    asset.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
        f'<metadata id="aad-scene" encoding="base64+gzip">{encoded}</metadata></svg>',
        encoding="utf-8",
    )
    # Keep the complete manifest, including part names and the original scene ID.
    config = json.dumps({"manifest": manifest, "asset": str(asset)}, ensure_ascii=True)
    config = config.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")
    html = STUB.read_text(encoding="utf-8").replace(
        "<head>", f"<head>\n<script>window.__AAD_EMBEDDED_SCENE__ = {config};</script>", 1
    )
    target = output_dir / "viewer.html"
    target.write_text(html, encoding="utf-8")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(export_bundle(args.scene_dir, args.output_dir))
