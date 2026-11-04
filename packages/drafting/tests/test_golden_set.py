"""Reference snapshots plus fresh, independent native measurements."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import ezdxf
import pytest

from aiad_drafting import make_drawing
from examples.golden_set import OUTPUT, cases, snapshot


@pytest.fixture(scope="module")
def references():
    result = cases()
    assert len(result) == 15
    assert {case.category for case in result} == {"laser", "plate", "turned", "assembly"}
    return result


def cylinders(shape):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED
    result = {}
    for index, face in enumerate(shape.faces()):
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            circle = surface.Cylinder()
            axis = circle.Axis()
            assert abs(axis.Direction().Z()) == pytest.approx(1)
            origin = axis.Location()
            low, high = sorted(origin.Z() + axis.Direction().Z() * station
                               for station in (surface.FirstVParameter(), surface.LastVParameter()))
            result[index] = {"radius": circle.Radius(), "center": (origin.X(), origin.Y()),
                             "low": low, "high": high, "interior": face.wrapped.Orientation() == TopAbs_REVERSED}
    return result


def geometry_bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, bounds, False, False)
    a, b = bounds.CornerMin(), bounds.CornerMax()
    return (a.X(), a.Y(), a.Z()), (b.X(), b.Y(), b.Z())


def assert_dimensions_from_native_geometry(case, result):
    from build123d import Compound
    from aiad_drafting.model2d import _entity_to_native
    native = getattr(case.ctx, "sheet", None)
    shape = native.to_shape() if native else case.ctx.shape
    low, high = geometry_bounds(shape)
    surfaces = cylinders(shape) if not native else {}
    holes = {}
    for record in result.dimensions:
        if record["kind"] == "hole_diameter" and not native:
            matching = [surfaces[index] for index in record["source"]["face_indices"]]
            unique = {(round(item["center"][1], 8), round(item["center"][0], 8), round(item["radius"], 8)): item for item in matching}
            for index, item in zip(record["hole_indices"], [unique[key] for key in sorted(unique)]):
                holes[index] = item
    for record in result.dimensions:
        kind, axis, value, source = record["kind"], record["axis"], record["value"], record["source"]
        if kind == "overall":
            index = {"x": 0, "y": 1, "z": 2}[axis]
            expected = high[index] - low[index]
        elif kind in {"hole_diameter", "hole_coordinate", "hole_spacing"}:
            if native:
                selected = []
                from OCP.BRepAdaptor import BRepAdaptor_Curve
                for cutout in source["cutouts"]:
                    entities = [_entity_to_native(native, native.entities[name]) for name in cutout["source_entities"]]
                    edge = entities[0].edges()[0]
                    circle = BRepAdaptor_Curve(edge.wrapped).Circle()
                    selected.append({"center": (circle.Location().X(), circle.Location().Y()), "radius": circle.Radius()})
            else:
                selected = [holes[index] for index in record["hole_indices"]]
            assert selected
            if kind == "hole_diameter":
                expected = 2 * selected[0]["radius"]
                assert all(2 * item["radius"] == pytest.approx(expected) for item in selected)
            elif kind == "hole_coordinate":
                index = 0 if axis == "x" else 1
                expected = selected[0]["center"][index] - low[index]
                assert all(item["center"][index] - low[index] == pytest.approx(expected) for item in selected)
            else:
                index = 0 if axis == "x" else 1
                expected = max(item["center"][index] for item in selected) - min(item["center"][index] for item in selected)
        elif kind.startswith("cylinder_"):
            matches = [surfaces[index] for index in source["face_indices"]]
            assert matches
            measured = matches[0]
            if kind == "cylinder_size":
                expected = measured["radius"] * (1 if record["display"].startswith("R ") else 2)
            elif kind == "cylinder_from":
                expected = measured["low"] - low[2]
            elif kind == "cylinder_to":
                expected = measured["high"] - low[2]
            else:
                expected = measured["high"] - measured["low"]
        elif kind == "profile_parameter":
            selected = [_entity_to_native(native, native.entities[name]) for name in source["entities"]]
            a, b = geometry_bounds(Compound(children=selected))
            if axis in {"x", "y"}:
                index = 0 if axis == "x" else 1
                expected = (a[index] + b[index]) / 2 - low[index]
            elif axis == "width":
                expected = min(b[0]-a[0], b[1]-a[1])
            elif axis == "length":
                expected = max(b[0]-a[0], b[1]-a[1])
            else:
                edge = max((edge for item in selected for edge in item.edges() if edge.geom_type.name == "LINE"), key=lambda edge: edge.length)
                direction = edge.end_point() - edge.start_point()
                expected = math.degrees(math.atan2(direction.Y, direction.X)) % 180
        else:
            pytest.fail(f"Unvalidated dimension kind {kind}")
        assert value == pytest.approx(expected, abs=1e-6), (case.name, record)


@pytest.mark.parametrize("number", range(15))
def test_reference_drawing_snapshot_geometry_layout_and_dxf(number, references, tmp_path):
    case = references[number]
    result = make_drawing(case.ctx, case.request, tmp_path / case.name)
    assert result.ok, [check for check in result.checks if check["status"] == "fail"]
    assert not any(check["status"] == "fail" for check in result.checks)
    assert result.sheet["layout_fits"]
    assert_dimensions_from_native_geometry(case, result)
    reference = json.loads((OUTPUT / case.name / "snapshot.json").read_text())
    assert snapshot(case, result) == reference
    document = ezdxf.readfile(result.artifacts["dxf"])
    assert document.dxfversion == "AC1027"
    assert not document.audit().errors
    assert all(path.is_file() and path.stat().st_size > 100 for path in (*result.artifacts.values(), result.preview))


def test_same_machine_drawing_vectors_pdf_and_pixels_are_repeatable(references, tmp_path):
    for number in (0, 4, 8, 12):
        case = references[number]
        first = make_drawing(case.ctx, case.request, tmp_path / f"first-{number}")
        second = make_drawing(case.ctx, case.request, tmp_path / f"second-{number}")
        for kind in ("svg", "pdf"):
            assert first.artifacts[kind].read_bytes() == second.artifacts[kind].read_bytes()
        assert first.preview.read_bytes() == second.preview.read_bytes()


def test_committed_reference_artifacts_match_manifest():
    manifest = json.loads((OUTPUT / "manifest.json").read_text())
    assert len(manifest["drawings"]) == 15
    independent = OUTPUT / "independent"
    reviewed = {entry["name"]: entry for entry in json.loads((independent / "validation.json").read_text())["drawings"]}
    assert set(reviewed) == {drawing["name"] for drawing in manifest["drawings"]}
    for drawing in manifest["drawings"]:
        assert drawing["ok"]
        for filename, expected in drawing["files"].items():
            path = OUTPUT / drawing["name"] / filename
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        evidence = reviewed[drawing["name"]]
        assert evidence["opened"] and evidence["previewed"]
        assert evidence["dxf_sha256"] == drawing["files"]["drawing.dxf"]
        assert (independent / evidence["rendered_pdf"]).read_bytes().startswith(b"%PDF-")
        assert (independent / (evidence["rendered_pdf"] + ".png")).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_root_command_regenerates_all_reference_formats_without_metadata_churn(tmp_path):
    from examples.golden_set import ROOT, REPO
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    outputs = [tmp_path/"first", tmp_path/"second"]
    for index, output in enumerate(outputs):
        environment["PYTHONHASHSEED"] = str(index+1)
        result = subprocess.run([sys.executable, str(ROOT/"examples/golden_set.py"), "--out", str(output)],
            cwd=REPO, env=environment, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    for first in outputs[0].rglob("*"):
        if first.is_file():
            assert first.read_bytes() == (outputs[1]/first.relative_to(outputs[0])).read_bytes(), first
