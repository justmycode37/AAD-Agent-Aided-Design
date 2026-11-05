from __future__ import annotations

from types import SimpleNamespace

from aad_quality.spec import spec_checks


def _ctx(spec, shape, *, units="mm", project_dir=None, material=None, metrics=None, params=None):
    return SimpleNamespace(
        spec=spec,
        shape=shape,
        units=units,
        project_dir=project_dir,
        material=material,
        metrics=metrics or {},
        params=params or {},
    )


def _plate_with_holes(spacing_x=46, spacing_y=26, diameter=4.4):
    from build123d import Box, Cylinder, Pos

    shape = Box(60, 40, 6)
    for x in (-spacing_x / 2, spacing_x / 2):
        for y in (-spacing_y / 2, spacing_y / 2):
            shape = shape - Pos(x, y, 0) * Cylinder(diameter / 2, 8)
    return shape


def test_bbox_and_solid_count_use_real_brep_geometry():
    from build123d import Box

    checks = spec_checks(
        _ctx(
            {
                "units": "mm",
                "requirements": [
                    {"id": "size", "bbox": {"x": "60 ± 0.01", "y": "40 ± 0.01", "z": "6 ± 0.01"}},
                    {"id": "body", "solids": 1},
                ],
            },
            Box(60, 40, 6),
        )
    )
    assert [check.status for check in checks] == ["pass", "pass"]
    assert all(check.expected and check.actual and check.fix_hint for check in checks)
    assert "± 0.01 mm" in checks[0].expected


def test_bbox_does_not_trust_context_metrics():
    from build123d import Box

    ctx = _ctx(
        {"requirements": [{"id": "size", "bbox": {"x": 10}}]},
        Box(12, 8, 4),
        metrics={"bbox": [10, 8, 4]},
        params={"width": 10},
    )
    check = spec_checks(ctx)[0]
    assert check.status == "fail"
    assert "12 mm" in check.actual


def test_bbox_compares_inches_and_unit_bearing_tolerances():
    from build123d import Box

    checks = spec_checks(
        _ctx(
            {
                "units": "in",
                "requirements": [{"id": "one_inch", "bbox": {"x": "1 ± 0.01 in"}}],
            },
            Box(25.4, 50.8, 76.2),
            units="mm",
        )
    )
    assert checks[0].status == "pass"
    assert "1 in ± 0.01 in" in checks[0].expected
    assert "1 in" in checks[0].actual


def test_bbox_converts_model_inches_to_spec_millimeters():
    from build123d import Box

    checks = spec_checks(
        _ctx(
            {"units": "mm", "requirements": [{"id": "inch_part", "bbox": {"x": "25.4 ± 0.001"}}]},
            Box(1, 2, 3),
            units="in",
        )
    )
    assert checks[0].status == "pass"
    assert "25.4 mm" in checks[0].actual


def test_hole_count_diameter_and_xy_grid_from_full_internal_cylinders():
    spec = {
        "units": "mm",
        "requirements": [
            {
                "id": "mounts",
                "holes": {"count": 4, "diameter": "4.4 ± 0.05", "pattern": {"grid": [46, 26]}},
            }
        ],
    }
    check = spec_checks(_ctx(spec, _plate_with_holes()))[0]
    assert check.status == "pass"
    assert "4 matching analytic hole axis/axes" in check.actual
    assert "X pitches [46.0]" in check.actual
    assert "Y pitches [26.0]" in check.actual


def test_wrong_hole_count_diameter_and_grid_fail():
    shape = _plate_with_holes(spacing_x=40, spacing_y=20, diameter=5.0)
    spec = {
        "requirements": [
            {"id": "wrong_mounts", "holes": {"count": 5, "diameter": "4.4 ± 0.05", "pattern": {"grid": [46, 26]}}}
        ]
    }
    check = spec_checks(_ctx(spec, shape))[0]
    assert check.status == "fail"
    assert "0 matching analytic hole axis/axes" in check.actual
    assert "5 mm" in check.actual
    assert check.expected and check.actual


def test_external_boss_is_not_counted_as_a_hole():
    from build123d import Box, Cylinder, Pos

    shape = Box(20, 20, 5) + Pos(0, 0, 4.5) * Cylinder(3, 4)
    check = spec_checks(_ctx({"requirements": [{"id": "no_bore", "holes": {"count": 0, "scope": "cylindrical"}}]}, shape))[0]
    assert check.status == "pass"
    assert "0 matching analytic hole axis/axes" in check.actual


def test_wrong_solid_count_fails_on_real_compound():
    from build123d import Box, Compound, Pos

    shape = Compound(children=[Box(4, 4, 4), Pos(8, 0, 0) * Box(4, 4, 4)])
    check = spec_checks(_ctx({"requirements": [{"id": "one_body", "solids": 1}]}, shape))[0]
    assert check.status == "fail"
    assert "2 solid(s)" in check.actual


def test_material_at_point_classifies_solid_and_void():
    from build123d import Box

    checks = spec_checks(
        _ctx(
            {
                "requirements": [
                    {"id": "inside", "material_at_point": {"point": [0, 0, 0], "expected": "solid"}},
                    {"id": "outside", "material_at_point": {"point": [8, 0, 0], "expected": "void"}},
                ]
            },
            Box(10, 10, 10),
        )
    )
    assert [check.status for check in checks] == ["pass", "pass"]
    assert "solid material" in checks[0].actual
    assert "void" in checks[1].actual


def test_point_on_boundary_is_skipped():
    from build123d import Box

    check = spec_checks(
        _ctx(
            {"requirements": [{"id": "edge", "material_at_point": {"point": [5, 0, 0], "expected": "solid"}}]},
            Box(10, 10, 10),
        )
    )[0]
    assert check.status == "skip"
    assert "boundary" in check.message


def test_global_min_wall_never_passes_from_bbox_or_sparse_evidence():
    from build123d import Box

    check = spec_checks(_ctx({"requirements": [{"id": "wall", "min_wall": 1.0}]}, Box(20, 10, 4)))[0]
    assert check.status == "skip"
    assert "cannot establish global minimum thickness" in check.actual


def test_missing_shape_is_an_explicit_skip():
    check = spec_checks(_ctx({"requirements": [{"id": "size", "bbox": {"x": 10}}]}, None))[0]
    assert check.status == "skip"
    assert check.actual.startswith("unknown")


def test_mass_is_skipped_without_identified_material_data():
    from build123d import Box

    check = spec_checks(_ctx({"requirements": [{"id": "mass", "mass_g": {"max": 100}}]}, Box(10, 10, 10)))[0]
    assert check.status == "skip"
    assert "mass cannot be measured" in check.message
    assert "g from" not in check.actual


def test_custom_measure_skips_without_s1_resolver(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "aad.engine.select", None)
    check = spec_checks(
        _ctx(
            {
                "requirements": [
                    {
                        "id": "fit",
                        "measure": {"kind": "distance", "a": "face(feature='a', where='max_z')", "b": "face(feature='b', where='min_z')"},
                        "equals": "12 ± 0.05",
                    }
                ]
            },
            None,
        )
    )[0]
    assert check.status == "skip"
    assert "resolver" in check.message


def test_custom_measure_uses_the_installed_s1_resolver():
    from build123d import Box

    requirement = {
        "id": "height",
        "measure": {"kind": "distance", "a": "face(where='max_z')", "b": "face(where='min_z')"},
        "equals": "4 ± 0.01",
    }
    ctx = _ctx({"requirements": [requirement]}, Box(10, 8, 4))
    assert spec_checks(ctx)[0].status == "pass"
    requirement["equals"] = "12 ± 0.01"
    assert spec_checks(ctx)[0].status == "fail"


def test_zero_difference_volume_is_diagnostic_not_a_pointwise_symmetry_proof():
    from build123d import Box

    check = spec_checks(
        _ctx({"requirements": [{"id": "mirror", "symmetric": {"plane": "YZ", "tolerance": "0.01 mm"}}]}, Box(10, 8, 6))
    )[0]
    assert check.status == "skip"
    assert "symmetric-difference volume 0 mm^3" in check.actual
    assert "kernel error bound" in check.actual


def test_project_contained_step_clearance_passes_and_wrong_limit_fails(tmp_path):
    from build123d import Box, Pos, export_step

    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    asset_path = asset_dir / "neighbor.step"
    export_step(Pos(9, 0, 0) * Box(2, 2, 2), str(asset_path))
    shape = Box(10, 10, 10)

    pass_check = spec_checks(
        _ctx(
            {"requirements": [{"id": "clearance", "clearance": {"with": "assets/neighbor.step", "min": 3}}]},
            shape,
            project_dir=tmp_path,
        )
    )[0]
    fail_check = spec_checks(
        _ctx(
            {"requirements": [{"id": "clearance", "clearance": {"with": "assets/neighbor.step", "min": 4}}]},
            shape,
            project_dir=tmp_path,
        )
    )[0]
    assert pass_check.status == "pass"
    assert "3 mm" in pass_check.actual
    assert fail_check.status == "fail"


def test_step_clearance_rejects_paths_outside_project(tmp_path):
    from build123d import Box

    outside_asset = tmp_path.parent / f"{tmp_path.name}_outside.step"
    outside_asset.write_text("not a STEP model", encoding="utf-8")
    check = spec_checks(
        _ctx(
            {"requirements": [{"id": "escape", "clearance": {"with": f"../{outside_asset.name}", "min": 1}}]},
            Box(2, 2, 2),
            project_dir=tmp_path,
        )
    )[0]
    assert check.status == "skip"
    assert "outside the project" in check.message
