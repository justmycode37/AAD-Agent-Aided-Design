#!/usr/bin/env python3
"""Repeated construction controls, not an agent or manufacturing benchmark."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import uuid

from aiad_quality import standard_parts
from evidence import summaries
from native_witnesses import geometry_witness

BASE = Path(__file__).resolve().parent
EXPECTED = {
    "fastener.iso4762.socket_head_cap_screw": (10.22, 10.22, 26),
    "bearing.deep_groove_ball.skt": (22, 22, 7),
    "thread.iso_metric_external": (6, 6, 8),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints():
    import bd_warehouse
    root = Path(bd_warehouse.__file__).parent
    paths = {"adapter": Path(standard_parts.__file__), "probe": Path(__file__),
             "validation/native_witnesses.py": BASE / "native_witnesses.py",
             "validation/evidence.py": BASE / "evidence.py", "validation/check_evidence.py": BASE / "check_evidence.py",
             **{f"upstream/{path.relative_to(root)}": path for path in sorted(root.rglob("*"))
                if path.is_file() and path.suffix in {".py", ".csv"}}}
    return {key: digest(path) for key, path in paths.items()}


def measure_artifact(artifact, part_id):
    from build123d import Shape, import_brep
    from OCP.Bnd import Bnd_Box
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt
    raw = import_brep(str(artifact)).wrapped
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(raw, box, False, False)
    low, high = box.CornerMin(), box.CornerMax()
    dims = [b - a for a, b in zip((low.X(), low.Y(), low.Z()), (high.X(), high.Y(), high.Z()))]
    valid = BRepCheck_Analyzer(raw).IsValid()
    solids = Shape.cast(raw).solids()
    def occupied(x, y, z):
        return any(BRepClass3d_SolidClassifier(s.wrapped, gp_Pnt(x, y, z), 1e-7).State() == TopAbs_IN for s in solids)
    probes = []
    thread_zone_dims = None
    if part_id.startswith("bearing."):
        z = (low.Z() + high.Z()) / 2
        probes = [{"point_mm": [3.99,0,z], "expected_solid": False},
                  {"point_mm": [4.01,0,z], "expected_solid": True}]
    elif part_id.startswith("fastener."):
        z = low.Z() + 10
        crop = BRepPrimAPI_MakeBox(gp_Pnt(-20,-20,-19),40,40,18).Shape()
        common = BRepAlgoAPI_Common(raw,crop)
        common.Build()
        if not common.IsDone():
            raise SystemExit('Could not independently isolate the screw thread zone')
        thread_bounds = Bnd_Box()
        BRepBndLib.AddOptimal_s(common.Shape(),thread_bounds,False,False)
        a,b = thread_bounds.CornerMin(),thread_bounds.CornerMax()
        thread_zone_dims = [b.X()-a.X(),b.Y()-a.Y()]
        probes = [{"point_mm": [2.3,0,z], "expected_solid": True},
                  {"point_mm": [3.01,0,z], "expected_solid": False}]
    for probe in probes:
        probe["actual_solid"] = occupied(*probe["point_mm"])
    witness = geometry_witness(raw, part_id)
    return {"valid": valid, "bbox_mm": dims, "thread_zone_xy_mm": thread_zone_dims,
            "solids": len(solids), "material_probes": probes, "geometry_witness":witness,
            "passed": valid and bool(solids) and (not part_id.startswith("fastener.") or len(solids)==1)
                      and witness['passed']
                      and all(abs(a-b)<=.01 for a,b in zip(dims,EXPECTED[part_id]))
                      and all(p["actual_solid"] == p["expected_solid"] for p in probes)
                      and (thread_zone_dims is None or all(abs(d-6)<=.01 for d in thread_zone_dims))}


def main():
    from build123d import export_brep
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--out", type=Path, default=BASE / "parts-evidence.json")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    args.out.unlink(missing_ok=True)
    before = fingerprints()
    run_id = str(uuid.uuid4())
    out = BASE / ".runs" / run_id
    out.mkdir(parents=True)
    records = []
    for part_id in EXPECTED:
        for repeat in range(1, args.repetitions+1):
            artifact = out / f"{part_id}-{repeat}.brep"
            export_brep(standard_parts.make_part(part_id),str(artifact))
            records.append({"id":part_id,"repeat":repeat,**measure_artifact(artifact,part_id),
                            "artifact":artifact.relative_to(BASE).as_posix(),"artifact_sha256":digest(artifact)})
    if before != fingerprints():
        raise SystemExit("Sources changed during construction; no completed evidence retained")
    data = {"run_id":run_id,"repetitions":args.repetitions,"complete":True,"passed":all(r["passed"] for r in records),
            "scope":"deterministic constructor/BREP round-trip controls; no agent-quality or manufacturing-compliance estimate",
            "reproduce":"PYTHONPATH=packages/quality/src packages/quality/.parts-venv/bin/python evals/library/probe_parts.py --repetitions 3",
            "versions":{p:version(p) for p in ("build123d","cadquery-ocp-novtk","bd_warehouse")},
            "source_sha256":before,"summaries":summaries(records,EXPECTED),"records":records}
    args.out.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:data[k] for k in ("complete","passed","scope","summaries")},indent=2))
    if not data["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
