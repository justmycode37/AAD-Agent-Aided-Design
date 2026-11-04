"""Writes a tcv-5 test scene from a real build123d part into the given directory.

Run with a Python that has build123d and ocp-tessellate (see packages/render).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from build123d import (
    Align, Axis, Box, BuildPart, CounterBoreHole, GridLocations, Hole, Locations, Plane, fillet,
)
from ocp_tessellate.convert import tessellate_group, to_ocpgroup


def bracket():
    with BuildPart() as bp:
        Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, -17, 6)):
            Box(60, 6, 34, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(Plane.XY.offset(6)):
            with GridLocations(46, 22, 2, 2):
                with Locations((0, 3)):
                    CounterBoreHole(radius=2.2, counter_bore_radius=4, counter_bore_depth=2.2, depth=6)
        with Locations(Plane(origin=(0, -14, 22), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):
            Hole(radius=13, depth=6)
        fillet(bp.edges().filter_by(Axis.Y).group_by(Axis.Z)[-1], radius=4)
        fillet(bp.edges().filter_by(Axis.X).group_by(Axis.Z)[1].sort_by(Axis.Y)[-1:], radius=3)
    return bp.part


class NumpyEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    part = bracket()
    bb = part.bounding_box()
    group, instances = to_ocpgroup(part, names=["bracket"], colors=["#b8c0cc"])
    meshed, shapes, _ = tessellate_group(group, instances, {"deviation": 0.1, "angular_tolerance": 0.2})

    def leaves(node):
        if node.get("parts"):
            for child in node["parts"]:
                yield from leaves(child)
        else:
            yield node

    for leaf in leaves(shapes):
        if isinstance(leaf.get("shape"), dict) and "ref" in leaf["shape"]:
            leaf["shape"] = meshed[leaf["shape"]["ref"]]
    solids = [leaf for leaf in leaves(shapes) if isinstance(leaf.get("shape"), dict)]
    shapes["feature_map"] = {
        leaf["id"]: {
            "faces": ["main"] * len(leaf["shape"]["triangles_per_face"]),
            "edges": ["main"] * len(leaf["shape"]["segments_per_edge"]),
        }
        for leaf in solids
    }
    data = json.dumps(shapes, cls=NumpyEncoder, separators=(",", ":")).encode()
    blob = gzip.compress(data, 6, mtime=0)
    scene_id = hashlib.sha256(blob).hexdigest()
    (out / f"{scene_id}.gz").write_bytes(blob)
    manifest = {
        "scene_id": scene_id,
        "project": "dev",
        "rev": 1,
        "kind": "3d",
        "format": "tcv-5",
        "encoding": "gzip",
        "units": "mm",
        "bbox": [bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z],
        "total_bytes": len(blob),
        "triangles": sum(len(leaf["shape"]["triangles"]) // 3 for leaf in solids),
        "parts": [{"path": leaf["id"], "name": leaf["name"]} for leaf in solids],
        "status": "draft",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({"valid": part.is_valid, "faces": len(part.faces()), "raw": len(data), "gz": len(blob), **manifest}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
