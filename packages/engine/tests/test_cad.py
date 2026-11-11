"""The CAD helpers, against the pinned kernel, with values worked out by hand."""
from __future__ import annotations

import math

import pytest

build123d = pytest.importorskip("build123d")
from build123d import Axis, Box, Compound, Cylinder, Edge, GeomType, Pos, Spline, Wire  # noqa: E402

from aad.cad import (  # noqa: E402
    CadOperationError,
    assembly,
    asset,
    configure_assets,
    delete_faces,
    exploded,
    motion_clearance,
    overlap_volume,
    pattern_along,
    press_pull,
    sew_solid,
    sheet_metal,
    variable_fillet,
)


def top(shape):
    return shape.faces().sort_by(Axis.Z)[-1]


def test_press_pull_adds_and_removes_exact_volume():
    block = Box(40, 30, 20)
    assert press_pull(block, top(block), 5).volume == pytest.approx(40 * 30 * 25)
    assert press_pull(block, top(block), -5).volume == pytest.approx(40 * 30 * 15)


def test_press_pull_refuses_curved_faces_and_zero():
    cylinder = Cylinder(5, 10)
    with pytest.raises(CadOperationError, match="planar"):
        press_pull(cylinder, cylinder.faces().filter_by(GeomType.CYLINDER)[0], 1)
    with pytest.raises(CadOperationError, match="zero"):
        press_pull(cylinder, top(cylinder), 0)


def test_delete_faces_heals_a_hole_and_a_fillet():
    block = Box(40, 30, 20)
    drilled = block - Cylinder(3, 40)
    healed = delete_faces(drilled, drilled.faces().filter_by(GeomType.CYLINDER))
    assert healed.volume == pytest.approx(block.volume)
    assert len(healed.faces()) == 6


def test_variable_fillet_removes_between_the_two_constant_fillets():
    block = Box(40, 30, 20)
    edge = block.edges().filter_by(Axis.Z)[0]
    removed = block.volume - variable_fillet(block, edge, 2, 5).volume
    per_radius_squared = (1 - math.pi / 4) * 20
    assert per_radius_squared * 4 < removed < per_radius_squared * 25


def test_variable_fillet_reports_an_impossible_radius():
    block = Box(4, 4, 4)
    with pytest.raises(CadOperationError):
        variable_fillet(block, block.edges().filter_by(Axis.Z)[0], 50, 60)


def test_sew_solid_closes_only_a_closed_shell():
    faces = list(Box(10, 10, 10).faces())
    closed = sew_solid(faces)
    assert closed.volume == pytest.approx(1000)
    assert not sew_solid(faces[:3]).solids()


def test_pattern_along_keeps_spacing_and_closed_paths_do_not_repeat():
    copies = pattern_along(Box(2, 2, 2), Spline((0, 0, 0), (50, 20, 0), (100, 0, 0)), 5)
    assert copies[0].center().X == pytest.approx(0, abs=1e-6)
    assert copies[-1].center().X == pytest.approx(100, abs=1e-6)
    ring = pattern_along(Box(2, 2, 2), Wire([Edge.make_circle(50)]), 4, align=False)
    assert len({(round(c.center().X, 3), round(c.center().Y, 3)) for c in ring}) == 4


def test_assembly_bom_groups_by_number_and_refuses_different_geometry():
    asm = assembly("stack")
    asm.add("a", Box(10, 10, 10), part_number="B-1")
    asm.add("b", Box(10, 10, 10), at=Pos(5, 0, 0), part_number="B-1")
    asm.add("c", Cylinder(2, 30), at=Pos(100, 0, 0))
    rows = asm.bom()
    assert [(row["part_number"], row["quantity"]) for row in rows] == [("B-1", 2), ("c", 1)]
    assert [(item["a"], item["b"]) for item in asm.interference()] == [("a", "b")]
    assert asm.interference()[0]["volume"] == pytest.approx(500)
    assert [child.label for child in asm.compound().children] == ["a", "b", "c"]
    asm.add("d", Box(1, 1, 1), part_number="B-1")
    with pytest.raises(CadOperationError, match="different geometry"):
        asm.bom()


def test_assembly_records_declared_joints_beside_bom_metadata():
    asm = assembly("stack")
    asm.add("frame", Box(10, 10, 2), part_number="FR-1")
    asm.add("bracket", Box(5, 5, 2), at=Pos(0, 0, 2), part_number="BR-1")
    asm.connect("frame", "bracket", "bolted", fasteners=["M3x8"], tolerance=0.1)

    record = asm.record()
    assert record["components"] == ["frame", "bracket"]
    assert len(record["bom"]) == 2
    assert record["joints"] == [{
        "a": "frame",
        "b": "bracket",
        "kind": "bolted",
        "details": {"fasteners": ["M3x8"], "tolerance": 0.1},
    }]


def test_assembly_connect_rejects_bad_references_and_non_json_details():
    asm = assembly("stack")
    asm.add("frame", Box(10, 10, 2))
    asm.add("bracket", Box(5, 5, 2), at=Pos(0, 0, 2))
    with pytest.raises(CadOperationError, match="unknown component"):
        asm.connect("frame", "missing", "bolted")
    with pytest.raises(CadOperationError, match="JSON-compatible"):
        asm.connect("frame", "bracket", "bolted", data=object())


def test_assembly_refuses_duplicate_names_and_empty_output():
    asm = assembly("x")
    with pytest.raises(CadOperationError, match="no components"):
        asm.compound()
    asm.add("a", Box(1, 1, 1))
    with pytest.raises(CadOperationError, match="already used"):
        asm.add("a", Box(1, 1, 1))


def test_exploded_moves_bodies_away_from_center():
    pair = Compound(children=[Pos(-10, 0, 0) * Box(2, 2, 2), Pos(10, 0, 0) * Box(2, 2, 2)])
    assert exploded(pair, 1.0).bounding_box().size.X == pytest.approx(pair.bounding_box().size.X + 20)
    with pytest.raises(CadOperationError):
        exploded(Box(1, 1, 1))


def test_motion_clearance_reports_each_pose_and_its_limit():
    report = motion_clearance(Pos(30, 0, 0) * Box(10, 10, 10), Box(10, 10, 10), [Pos(0, 0, 0), Pos(-10, 0, 0), Pos(-25, 0, 0)])
    assert [pose["clearance"] for pose in report["poses"][:2]] == pytest.approx([20, 10])
    assert report["poses"][2]["overlap_volume"] == pytest.approx(500)
    assert report["collides"] and report["worst_pose"] == 2
    assert report["definition"] == "finite_pose_samples_not_continuous_sweep"
    assert overlap_volume(Box(1, 1, 1), Pos(50, 0, 0) * Box(1, 1, 1)) == 0


def test_sheet_metal_flat_length_matches_neutral_axis_volume():
    # At k = 0.5 the neutral axis is the mid-surface, so volume is exactly t * w * flat length.
    part = sheet_metal([40, 30], [90], width=25, thickness=2, inner_radius=3, k_factor=0.5)
    assert part.flat_length == pytest.approx(70 + (math.pi / 2) * 4)
    assert part.folded.volume == pytest.approx(2 * 25 * part.flat_length)
    assert len(part.folded.solids()) == 1
    assert part.bends[0]["allowance"] == pytest.approx((math.pi / 2) * 4)


def test_sheet_metal_folds_the_right_way_and_keeps_holes_in_both_forms():
    up = sheet_metal([40, 30], [90], width=25, thickness=2, inner_radius=3).folded.bounding_box()
    down = sheet_metal([40, 30], [-90], width=25, thickness=2, inner_radius=3).folded.bounding_box()
    assert up.max.Z > 30 and down.min.Z < -30
    plain = sheet_metal([40, 30, 20], [90, -90], width=25, thickness=2, inner_radius=3)
    holed = sheet_metal([40, 30, 20], [90, -90], width=25, thickness=2, inner_radius=3,
                        holes=[(0, 20, 12.5, 5), (1, 15, 12.5, 4)])
    expected = math.pi * (2.5**2 + 2**2) * 2
    assert plain.folded.volume - holed.folded.volume == pytest.approx(expected, rel=1e-6)
    assert holed.holes_flat[0] == (20, 12.5, 5)
    assert holed.holes_flat[1][0] == pytest.approx(40 + holed.bends[0]["allowance"] + 15)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (([10, 10], []), "one fewer"),
        (([10, 10], [0]), "nonzero"),
        (([10, 10], [180]), "under 180"),
        (([10, -1], [90]), "positive"),
    ],
)
def test_sheet_metal_refuses_bad_bends(args, message):
    with pytest.raises(CadOperationError, match=message):
        sheet_metal(*args, width=10, thickness=1, inner_radius=1)


def test_sheet_metal_refuses_a_hole_past_the_flange():
    with pytest.raises(CadOperationError, match="does not fit"):
        sheet_metal([10], [], width=10, thickness=1, inner_radius=1, holes=[(0, 9, 5, 4)])


@pytest.mark.parametrize("name", ["../secret.step", "/etc/passwd", "a/b.step", "", ".."])
def test_asset_names_cannot_leave_the_staged_directory(tmp_path, name):
    configure_assets(tmp_path)
    with pytest.raises(CadOperationError):
        asset(name)


def test_asset_requires_a_staged_file(tmp_path):
    configure_assets(tmp_path)
    with pytest.raises(CadOperationError, match="aad_import"):
        asset("missing.step")


def test_one_shape_can_become_several_named_components():
    source = Box(1, 1, 1)
    asm = assembly("x")
    asm.add("a", source)
    asm.add("b", source, at=Pos(5, 0, 0))
    assert [child.label for child in asm.compound().children] == ["a", "b"]
    assert source.label != "b"


def test_asset_staging_copies_regular_files_and_skips_links(tmp_path):
    from aad.engine.jobs import _stage_assets

    project, job = tmp_path / "project", tmp_path / "job"
    (project / "imports").mkdir(parents=True)
    job.mkdir()
    (tmp_path / "secret.step").write_text("secret")
    (project / "imports" / "part.step").write_text("real")
    (project / "imports" / ".partial.tmp").write_text("hidden")
    (project / "imports" / "link.step").symlink_to(tmp_path / "secret.step")
    _stage_assets(project, job)
    assert [path.name for path in (job / "assets").iterdir()] == ["part.step"]


def test_asset_staging_ignores_a_linked_imports_folder(tmp_path):
    from aad.engine.jobs import _stage_assets

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.step").write_text("secret")
    project, job = tmp_path / "project", tmp_path / "job"
    project.mkdir()
    job.mkdir()
    (project / "imports").symlink_to(outside)
    _stage_assets(project, job)
    assert not (job / "assets").exists()


def test_bom_refuses_one_number_with_two_materials():
    asm = assembly("x")
    asm.add("a", Box(1, 1, 1), part_number="P", material="steel_1018")
    asm.add("b", Box(1, 1, 1), part_number="P", material="pla")
    with pytest.raises(CadOperationError, match="materials"):
        asm.bom()


def test_direct_edits_refuse_a_result_that_did_not_do_the_job():
    block = Box(10, 10, 10)
    with pytest.raises(CadOperationError, match="unchanged"):
        delete_faces(block, top(block))
    with pytest.raises(CadOperationError, match="whole body"):
        press_pull(block, top(block), -20)


def _exported(tmp_path, units, fmt):
    from aad.engine.fileio import export
    from aad.engine.types import ModelContext

    ctx = ModelContext(job_dir=tmp_path, units=units, shape=Box(1, 1, 1))
    return export(ctx, fmt, tmp_path / f"cube.{fmt}")


@pytest.mark.parametrize("fmt", ["step", "iges"])
def test_inch_files_keep_their_size_in_inch_and_mm_projects(tmp_path, fmt):
    path = _exported(tmp_path, "in", fmt)
    configure_assets(tmp_path, "in")
    assert asset(path.name).bounding_box().size.X == pytest.approx(1, abs=1e-6)
    configure_assets(tmp_path, "mm")
    assert asset(path.name).bounding_box().size.X == pytest.approx(25.4, abs=1e-5)
    with pytest.raises(CadOperationError, match="declare their own units"):
        asset(path.name, source_units="in")


def test_unitless_asset_uses_stated_source_units(tmp_path):
    path = _exported(tmp_path, "mm", "brep")
    configure_assets(tmp_path, "mm")
    assert asset(path.name, source_units="in").bounding_box().size.X == pytest.approx(25.4)
    assert asset(path.name).bounding_box().size.X == pytest.approx(1)
    with pytest.raises(CadOperationError, match="source_units"):
        asset(path.name, source_units="ft")
