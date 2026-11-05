"""M0 scene writer, used only when aad_render is not installed. Runs in its own process.

Usage: python -m aad_platform.stubs.scene <part.brep> <out_dir> <project> <rev> <units>
Writes out_dir/scene.bin.gz and out_dir/scene.json.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    brep, out_dir, project, rev, units = argv
    from build123d import import_brep

    shape = import_brep(brep)
    bb = shape.bounding_box()
    tolerance = max(bb.diagonal * 1e-3, 1e-3)
    vertices, triangles = shape.tessellate(tolerance, 0.3)
    flat_vertices = [round(c, 5) for v in vertices for c in (v.X, v.Y, v.Z)]
    flat_triangles = [i for tri in triangles for i in tri]
    bbox = [bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z]
    tree = {
        "version": 3,
        "name": project,
        "id": f"/{project}",
        "loc": [[0, 0, 0], [0, 0, 0, 1]],
        "parts": [
            {
                "id": f"/{project}/main",
                "name": "main",
                "type": "shapes",
                "subtype": "solid",
                "shape": {"vertices": flat_vertices, "triangles": flat_triangles},
                "state": [1, 1],
                "color": "#6f8fb3",
                "alpha": 1.0,
                "loc": [[0, 0, 0], [0, 0, 0, 1]],
            }
        ],
        "feature_map": {},
    }
    payload = gzip.compress(json.dumps(tree, separators=(",", ":")).encode(), mtime=0)
    scene_id = hashlib.sha256(payload).hexdigest()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.bin.gz").write_bytes(payload)
    manifest = {
        "scene_id": scene_id,
        "project": project,
        "rev": int(rev),
        "kind": "3d",
        "format": "tcv-5",
        "encoding": "gzip",
        "units": units,
        "bbox": bbox,
        "total_bytes": len(payload),
        "triangles": len(triangles),
        "parts": [{"path": f"/{project}/main", "name": "main"}],
        "status": "draft",
    }
    (out / "scene.json").write_text(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
