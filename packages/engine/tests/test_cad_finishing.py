"""Finishing helpers preserve topology ownership and expose failed blends."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest


def _build(source: str, root: Path, *, spec: dict | None = None):
    return run_build(BuildRequest(
        source=source,
        project_dir=root,
        job_dir=root / "job",
        spec=spec or {},
        timeout_s=120,
    ))


def test_blend_retries_explicit_radius_and_tracks_feature_metadata(tmp_path):
    result = _build(
        '''from aad import *
with part("coupon") as bp:
    with feature("stock"):
        Box(10, 10, 10)
    with feature("edge_blend"):
        outcome = blend(bp.part, [bp.edges()], radius=8, fallback=(1,))
result = bp.part
''',
        tmp_path,
    )
    assert result.ok, result.failure
    feature = next(item for item in result.context.features if item.name == "edge_blend")
    report, = feature.metadata["finishing_reports"]
    assert report["counts"] == {"applied": 0, "reduced": 12, "failed": 0}
    assert [attempt["radius"] for attempt in report["groups"][0]["attempts"]] == [8, 1]
    assert all(edge["status"] == "reduced" and edge["applied_radius"] == 1 for edge in report["edges"])
    assert not any(check.id.startswith("blend_unresolved_edges") for check in result.checks)


def test_impossible_blend_radius_is_reported_as_build_warning(tmp_path):
    result = _build(
        '''from aad import *
with part("coupon") as bp:
    with feature("stock"):
        Box(10, 10, 10)
    with feature("edge_blend"):
        outcome = blend(bp.part, bp.edges(), radius=100, fallback=(50,))
result = bp.part
''',
        tmp_path,
    )
    assert result.ok, result.failure
    feature = next(item for item in result.context.features if item.name == "edge_blend")
    report, = feature.metadata["finishing_reports"]
    assert len(report["edges"]) == 12
    assert report["counts"]["failed"] == 12
    warning, = [check for check in result.checks if check.id.startswith("blend_unresolved_edges")]
    assert warning.status == "warn"
    assert warning.feature == "edge_blend"
    assert "12 requested edge(s) unresolved" in warning.message


def test_finishing_helpers_run_outside_a_builder(tmp_path):
    result = _build(
        '''from aad import *
shape = Box(20, 20, 10)
front = shape.faces().sort_by(Axis.X)[-1]
shape = draft_faces(shape, front, 2, Plane.XY)
profile = Face(Wire.make_polygon([(-8, -1, 5), (8, -1, 5), (8, 1, 5), (-8, 1, 5)], close=True))
shape = rib(shape, profile, (0, 0, 3))
shape = boss(shape, (0, 0, 5), radius=4, height=4, hole=2, root_fillet=0.5, hole_depth=1)
seam = Edge.make_line((-8, 0, 5), (8, 0, 5))
shape = split_flange(shape, seam, width=3, thickness=2, bolt_diameter=2, bolt_count=3, bolt_axis=(0, 0, 1), end_margin=2)
outcome = blend(shape, shape.edges().filter_by(Axis.Z)[:1], radius=0.25, fallback=(0.1,))
result = outcome
''',
        tmp_path,
    )
    assert result.ok, result.failure
    assert result.context.metrics["solids"] == 1
    assert result.context.meta["finishing_reports"][0]["edges"][0]["status"] in {"applied", "reduced"}


def test_kernel_refusal_from_draft_uses_an_existing_kernel_failure_code(tmp_path):
    result = _build(
        "from aad import *\nshape = Sphere(5)\nresult = draft_faces(shape, shape.faces()[0], 2)\n",
        tmp_path,
    )
    assert not result.ok
    assert result.failure.code.startswith("KERNEL_")
    assert result.failure.phase == "kernel"


def test_cast_housing_fixture_passes_aad_build_and_has_section_wall_evidence(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    source = (repo / "fixtures/cast_housing/model.py").read_text(encoding="utf-8")
    spec = {
        "units": "mm",
        "requirements": [
            {"id": "one_connected_casting", "solids": 1},
            {"id": "shell_wall_material", "material_at_point": {"point": [0, 32, 30], "expected": "solid"}},
            {"id": "shell_cavity_void", "material_at_point": {"point": [0, 0, 30], "expected": "void"}},
            {"id": "outside_envelope_void", "material_at_point": {"point": [50, 0, 30], "expected": "void"}},
        ],
    }
    result = _build(source, tmp_path, spec=spec)
    assert result.ok, result.failure
    hard_failures = [check for check in result.checks if check.suite == "hard" and check.level == "error" and check.status == "fail"]
    assert not hard_failures
    # Spec checks need aad_quality, which the engine-only CI job does not install; e2e runs them.
    if importlib.util.find_spec("aad_quality") is not None:
        spec_checks = {check.id: check for check in result.checks if check.suite == "spec"}
        assert all(spec_checks[name].status == "pass" for name in (
            "one_connected_casting", "shell_wall_material", "shell_cavity_void", "outside_envelope_void",
        ))
    features = {item.name: item for item in result.context.features}
    assert {"outer_loft", "shell_walls", "bearing_seat", "radial_ribs", "split_flange", "mounting_boss", "edge_blends"} <= set(features)
    blend_report, = features["edge_blends"].metadata["finishing_reports"]
    assert blend_report["feature"] == "edge_blends"
    assert len(blend_report["edges"]) == blend_report["counts"]["applied"] + blend_report["counts"]["reduced"] + blend_report["counts"]["failed"]

    from build123d import Plane, import_brep, section

    shape = import_brep(result.artifacts["part.brep"])
    section_view = section(obj=shape, section_by=Plane.XY, height=30)
    assert section_view.is_valid
    assert len(section_view.wires()) == 2
    assert section_view.area > 0
