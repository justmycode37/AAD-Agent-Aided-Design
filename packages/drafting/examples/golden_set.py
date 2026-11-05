"""Reproducible reference drawings from native CAD, with semantic snapshots."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build123d import Align, Box, Compound, Cylinder, Pos

from aad_drafting import DrawingRequest, DrawingView, make_drawing, sheet2d
from examples.first_sheet import build_sample_context
from examples.layout_samples import build_dense_mixed_hole_plate, build_simple_turned_part, build_two_part_assembly
from examples.planar_sheet import model as native_panel


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
OUTPUT = ROOT / "samples" / "golden"


@dataclass
class Case:
    name: str
    category: str
    ctx: SimpleNamespace
    request: DrawingRequest


def part(shape, name, units="mm", children=None):
    return SimpleNamespace(kind="assembly" if children else "part", shape=shape, units=units,
                           name=name, process="cnc_3axis", children=children or [])


def planar(sheet):
    return SimpleNamespace(kind="2d", sheet=sheet, shape=None, units=sheet.units, name=sheet.name, process="laser")


def obround_panel():
    sheet = sheet2d("obround control panel")
    sheet.rectangle("outline", 90, 50, origin=(-45, -25))
    for i, (x, y) in enumerate(((-35, -15), (35, -15), (-35, 15), (35, 15))):
        sheet.circle(f"mount{i}", (x, y), 2)
    sheet.line("slot_lower", (-12, -3), (12, -3))
    sheet.arc("slot_right", (12, 0), 3, -90, 90)
    sheet.line("slot_upper", (12, 3), (-12, 3))
    sheet.arc("slot_left", (-12, 0), 3, 90, 270)
    return sheet


def ring():
    sheet = sheet2d("laser flange ring")
    sheet.circle("outer", (0, 0), 30)
    sheet.circle("bore", (0, 0), 9)
    for index, center in enumerate(((-20, 0), (20, 0), (0, -20), (0, 20))):
        sheet.circle(f"mount{index}", center, 2.5)
    return sheet


def fold_strip():
    sheet = sheet2d("scored fold strip")
    sheet.rectangle("outline", 120, 40)
    sheet.line("fold1", (40, 0), (40, 40), layer="score")
    sheet.line("fold2", (80, 0), (80, 40), layer="score")
    sheet.text("label", "AAD", (12, 18), 3)
    return sheet


def fixture():
    # Use S1's actual fixture source once it is on main, without modifying it.
    engine = str(REPO / "packages" / "engine" / "src")
    previous_path, bytecode = list(sys.path), sys.dont_write_bytecode
    try:
        sys.path.insert(0, engine)
        sys.dont_write_bytecode = True
        from aad._runtime import configure_params
        configure_params()
        namespace = runpy.run_path(str(REPO / "fixtures" / "models" / "fan_mount.py"))
        return part(namespace["fan_mount"].part, "S1 fan mounting plate")
    finally:
        sys.path[:] = previous_path
        sys.dont_write_bytecode = bytecode


def stacked_shaft(segments, bore=None, name="turned shaft"):
    shape = None
    z = 0
    for radius, length in segments:
        cylinder = Pos(0, 0, z) * Cylinder(radius, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
        shape = cylinder if shape is None else shape + cylinder
        z += length
    if bore:
        shape -= Pos(0, 0, -1) * Cylinder(bore, z + 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part(shape, name)


def two_pin_assembly():
    base = Box(90, 50, 8)
    pins = []
    for x in (-25, 25):
        base -= Pos(x, 0, 0) * Cylinder(4.1, 10)
        pins.append(Pos(x, 0, 7) * Cylinder(4, 14))
    children = [{"name": "Base", "part_number": "BASE-02", "shape": base}]
    children += [{"name": f"Pin {i+1}", "part_number": f"PIN-{i+1}", "shape": pin} for i, pin in enumerate(pins)]
    return part(Compound(children=[base, *pins]), "Twin locating pin assembly", children=children)


def spacer_assembly():
    base = Box(80, 50, 8) - Cylinder(6.1, 10)
    spacer = Pos(0, 0, 8) * (Cylinder(11, 8) - Cylinder(6.1, 10))
    pin = Pos(0, 0, 9) * Cylinder(6, 22)
    children = [{"name": label, "part_number": number, "shape": shape} for label, number, shape in
                (("Base", "BASE-03", base), ("Spacer", "SPACER-01", spacer), ("Pin", "PIN-03", pin))]
    return part(Compound(children=[base, spacer, pin]), "Spacer and locating pin assembly", children=children)


def cases():
    result = []
    def add(name, category, ctx, *, sheet="A3", style="iso", views=None, notes=(), bom=False):
        index = len(result) + 1
        req = DrawingRequest(sheet=sheet, style=style, dimensions="auto",
            views=views or ("front", "top", "right", "iso"),
            title_block={"title": ctx.name, "drawing_no": f"AAD-G{index:02d}", "revision": "A"},
            notes=notes, revisions=({"revision": "A", "date": "2026-11-04", "description": "Reference drawing", "by": "AAD"},), bom=bom)
        result.append(Case(name, category, ctx, req))
    add("01-native-laser-panel", "laser", planar(native_panel()))
    add("02-obround-panel", "laser", planar(obround_panel()))
    add("03-flange-ring", "laser", planar(ring()))
    add("04-scored-strip", "laser", planar(fold_strip()), sheet="tabloid", style="asme")
    add("05-s1-fan-mount", "plate", fixture(), sheet="A4")
    ctx = build_sample_context()
    add("06-asymmetric-plate", "plate", ctx, sheet="letter", style="asme")
    add("07-dense-fixture-plate", "plate", build_dense_mixed_hole_plate(), sheet="A3")
    add("08-section-detail-plate", "plate", build_sample_context(), sheet="A2", views=(
        "front", "top", "right", DrawingView("section a-a", kind="section", plane="XY", offset=0, parent="front"),
        DrawingView("detail b", kind="detail", parent="top", center=(-27, -15), radius=7, scale=2)))
    add("09-stepped-shaft", "turned", build_simple_turned_part(), sheet="A4", views=("front", "top", "iso"))
    add("10-bored-spacer", "turned", stacked_shaft([(16, 20)], bore=5, name="Bored cylindrical spacer"), views=("front", "top", "iso"))
    add("11-shouldered-bushing", "turned", stacked_shaft([(20, 8), (12, 20)], bore=4, name="Shouldered bushing"), views=("front", "top", "iso"))
    add("12-grooved-spindle", "turned", stacked_shaft([(8, 15), (6, 4), (10, 11), (5, 10)], name="Grooved stepped spindle"), sheet="A4", views=("front", "top", "iso"))
    add("13-locating-fixture", "assembly", build_two_part_assembly(), bom=True, style="asme")
    add("14-twin-pin-fixture", "assembly", two_pin_assembly(), bom=True)
    add("15-spacer-stack", "assembly", spacer_assembly(), bom=True, sheet="A2")
    return result


def snapshot(case, drawing):
    def rounded(value):
        if isinstance(value, float):
            return round(value, 8)
        if isinstance(value, dict):
            return {key: rounded(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [rounded(item) for item in value]
        return value
    return rounded({"name": case.name, "category": case.category, "units": drawing.sheet["units"],
        "sheet": drawing.sheet["name"], "hole_count": drawing.sheet["hole_count"],
        "dimensions": [{key: record.get(key) for key in ("id", "kind", "axis", "value", "unit", "display", "count")} for record in drawing.dimensions],
        "views": [{key: view.get(key) for key in ("name", "kind", "visible_edges", "hidden_edges")} for view in drawing.views],
        "bom": drawing.bom,
        "checks": [{"id": check["id"], "status": check["status"]} for check in drawing.checks]})


@contextmanager
def fixed_reference_metadata():
    """Remove clock/GUID churn from reference DXFs without changing geometry."""
    import ezdxf
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        yield
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous


def generate(output=OUTPUT, selected=None):
    with fixed_reference_metadata():
        return _generate(output, selected)


def _generate(output, selected):
    entries = []
    for case in cases():
        if selected and case.name not in selected:
            continue
        result = make_drawing(case.ctx, case.request, output / case.name)
        # ezdxf discovers some CLASS records through a set, so their order
        # varies with Python's hash seed even when all geometry is identical.
        # Canonicalize reference-only metadata before computing file hashes.
        import ezdxf
        document = ezdxf.readfile(result.artifacts["dxf"])
        document.classes.add_required_classes(document.dxfversion)
        document.classes.classes = dict(sorted(document.classes.classes.items()))
        if document.audit().errors:
            raise ValueError(f"Reference DXF audit failed for {case.name}.")
        document.saveas(result.artifacts["dxf"])
        record = snapshot(case, result)
        (output / case.name / "snapshot.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (*result.artifacts.values(), result.preview)}
        entries.append({"name": case.name, "category": case.category, "ok": result.ok,
                        "dimensions": len(result.dimensions), "files": hashes})
        print(case.name, "PASS" if result.ok else "FAIL", len(result.dimensions), flush=True)
        for check in result.checks:
            if check["status"] == "fail":
                print(" ", check, flush=True)
    (output / "manifest.json").write_text(json.dumps({"schema": 1, "drawings": entries}, indent=2, sort_keys=True) + "\n")
    return entries


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--only", nargs="*")
    arguments = parser.parse_args()
    results = generate(arguments.out, set(arguments.only) if arguments.only else None)
    if any(not entry["ok"] for entry in results):
        raise SystemExit(1)
