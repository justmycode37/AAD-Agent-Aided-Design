"""Real-geometry and bounded-input regressions from the independent S5 review."""
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Plane, Pos, Rot, mirror

from aiad_quality.schema import SpecValidationError, load_spec
from aiad_quality.spec import spec_checks


def check(shape, requirement):
    return spec_checks(SimpleNamespace(shape=shape, units='mm', spec={'requirements': [dict(id='review', **requirement)]}))[0]


def plate(d=4.4, sx=46, sy=26):
    shape = Box(60, 40, 6)
    for x in (-sx/2, sx/2):
        for y in (-sy/2, sy/2):
            shape -= Pos(x, y, 0) * Cylinder(d/2, 8)
    return shape


def test_mirrored_boss_never_counts_as_a_hole():
    result = check(mirror(Cylinder(10, 5), Plane.YZ), {'holes': {'count': 1, 'diameter': 20}})
    assert result.status == 'fail', result


def test_mirrored_real_holes_keep_their_count():
    result = check(mirror(plate(), Plane.YZ), {'holes': {'count': 4, 'diameter': '4.4 ± 0.1'}})
    assert result.status == 'pass', result


def test_mount_holes_can_coexist_with_a_larger_center_bore():
    result = check(plate() - Cylinder(10, 8), {'holes': {'count': 4, 'diameter': '4.4 ± 0.1', 'pattern': {'grid': [46, 26]}}})
    assert result.status == 'pass', result


def test_coaxial_groove_does_not_split_one_bore_into_two():
    shape = Box(30, 30, 10) - Cylinder(5, 12) - Cylinder(7, 2)
    result = check(shape, {'holes': {'count': 1, 'diameter': '10 ± 0.1'}})
    assert result.status == 'pass', result


@pytest.mark.parametrize('sx,sy', [(23.7, 11.4), (45.6, 22.8), (46, 26)])
def test_decimal_grid_uses_kernel_resolution(sx, sy):
    result = check(plate(sx=sx, sy=sy), {'holes': {'count': 4, 'pattern': {'grid': [sx, sy]}}})
    assert result.status == 'pass', result


@pytest.mark.parametrize('delta,expected', [(2e-7, 'pass'), (2e-5, 'fail')])
def test_exact_lengths_allow_only_a_declared_kernel_resolution(delta, expected):
    assert check(Box(40 + delta, 10, 10), {'bbox': {'x': 40}}).status == expected


def test_sampled_thin_wall_is_a_failure_even_without_global_proof():
    result = check(Box(30, 20, 0.5), {'min_wall': 2})
    assert result.status == 'fail', result
    assert '0.5' in result.actual


def test_separated_mirror_is_a_proven_symmetry_violation():
    result = check(Pos(500, 0, 0) * Box(10, 10, 10), {'symmetric': {'plane': 'YZ', 'tolerance': 0.01}})
    assert result.status == 'fail', result


@pytest.mark.parametrize('text', ['074', '1:00', '1:00.0'])
def test_ambiguous_yaml_11_numbers_are_rejected(text):
    with pytest.raises(SpecValidationError):
        load_spec('requirements: [{id: size, bbox: {x: ' + text + '}}]')


@pytest.mark.timeout(3)
@pytest.mark.parametrize('source', ['[' * 5000, '{' * 5000, 'requirements: ' + '[' * 5000 + '0' + ']' * 5000])
def test_deep_yaml_is_a_diagnostic_instead_of_a_crash(source):
    with pytest.raises(SpecValidationError):
        load_spec(source)
    assert spec_checks(SimpleNamespace(spec=source, units='mm', shape=None))[0].status == 'skip'


@pytest.mark.parametrize('style', ['quoted', 'block'])
def test_brackets_inside_yaml_scalar_text_are_not_nesting(style):
    import json

    text = '[' * 5000 + '{' * 5000
    scalar = json.dumps(text) if style == 'quoted' else '|-\n  ' + text
    assert load_spec('material: ' + scalar + '\nrequirements: []\n')['material'] == text


@pytest.mark.timeout(3)
def test_alias_expansion_is_bounded():
    source = 'a: &a [1, 1]\n'
    for i in range(1, 25):
        source += f'a{i}: &a{i} [*' + ('a' if i == 1 else f'a{i-1}') + ', *' + ('a' if i == 1 else f'a{i-1}') + ']\n'
    with pytest.raises(SpecValidationError):
        load_spec(source)


@pytest.mark.parametrize('through,expected', [(True, 'fail'), (False, 'pass')])
def test_blind_dimples_require_an_explicit_blind_scope(through, expected):
    shape = Box(20, 20, 6) - Pos(0, 0, 3) * Cylinder(2, 0.02)
    result = check(shape, {'holes': {'count': 1, 'diameter': 4, 'through': through}})
    assert result.status == expected, result


def test_sealed_voids_are_not_holes_even_in_blind_scope():
    result = check(Box(20, 20, 6) - Cylinder(2, 2), {'holes': {'count': 1, 'through': False}})
    assert result.status == 'fail', result


def test_cylindrical_scope_accepts_fillets_without_hiding_a_missing_bore():
    from build123d import Axis, fillet
    shape = plate()
    shape = fillet(shape.edges().filter_by(Axis.Z), radius=1)
    assert check(shape, {'holes': {'count': 5, 'scope': 'cylindrical'}}).status == 'fail'
    assert check(shape, {'holes': {'count': 4, 'scope': 'cylindrical'}}).status == 'pass'


def process_check(shape, process, suffix, **settings):
    from aiad_quality.process import process_checks
    ctx = SimpleNamespace(shape=shape, units='mm', process=process, spec={'process_settings': settings}, features=[])
    return next(c for c in process_checks(ctx) if c.id.endswith(suffix))


def test_declared_process_defaults_to_blocking_a_blocked_counterbore():
    from pathlib import Path
    source = Path(__file__).resolve().parents[3] / 'evals/teardown/tasks/blocked_counterbore.py'
    ns = {}
    exec(compile(source.read_text(), str(source), 'exec'), ns)
    result = process_check(ns['result'], 'cnc_3axis', 'hole_approach_probe', tool_diameter_mm=3, tool_axis=[0,0,1])
    assert result.status == 'fail' and result.level == 'error', result


def test_through_hole_needs_only_one_clear_approach():
    shape = plate() + Pos(-23, -13, 10) * Box(10, 10, 2)
    result = process_check(shape, 'cnc_3axis', 'hole_approach_probe', tool_diameter_mm=3, tool_axis=[0,0,1])
    assert result.status == 'pass', result


def test_cavity_floor_does_not_block_the_exterior_lid_entry():
    # A blind enclosure, with a through hole in its lid and a nearby cavity
    # floor that the inner-mouth probe would hit.
    shape = Box(30, 30, 20) - Box(24, 24, 14) - Pos(0, 0, 8) * Cylinder(2, 6)
    result = process_check(shape, 'cnc_3axis', 'hole_approach_probe', tool_diameter_mm=3, tool_axis=[0,0,1])
    assert result.status == 'pass', result


def test_sampled_walls_cannot_certify_a_narrow_unsampled_web():
    from aiad_quality.spec import verification_ready
    from aiad_quality.process import process_checks
    from aiad_quality._checks import make_check
    shape = Box(30, 30, 3) + Pos(0,0,4) * Box(.3, 2, 5)
    spec = {'process':'fdm_pla', 'process_settings':{'min_wall_mm':2}, 'requirements':[{'id':'body','solids':1}]}
    ctx = SimpleNamespace(shape=shape, units='mm', process='fdm_pla', spec=spec, features=[])
    checks = spec_checks(ctx) + process_checks(ctx)
    hard = [make_check(id='test-only', suite='hard', level='error', status='pass', message='test hard gate')]
    assert not verification_ready(spec, checks, hard, process=ctx.process)
    wall = next(c for c in checks if c.id.endswith('wall_thickness'))
    assert wall.status != 'pass', wall


def test_complete_rectangular_geometry_proves_the_absence_of_cnc_cavities():
    from aiad_quality.process import process_checks
    ctx = SimpleNamespace(shape=Box(10,10,3), units='mm', process='cnc_3axis', spec={}, features=[])
    checks = process_checks(ctx)
    assert checks and all(c.status == 'pass' for c in checks), checks


def test_resolver_metadata_is_measured_only_through_its_live_native_binding(monkeypatch):
    import sys
    from aiad.engine.types import Entity
    shape = Box(10, 10, 10)
    selected = shape.faces()[0]
    entity = Entity(kind='face', index=0)
    def bind(ctx, candidate):
        assert ctx.shape is shape and candidate is entity
        return selected
    monkeypatch.setitem(sys.modules, 'aiad.engine.select', SimpleNamespace(resolve=lambda *a, **k: [entity], entity_shape=bind))
    result = check(shape, {'measure':{'kind':'area', 'a':'face(nth=0)'}, 'equals':'100 mm^2'})
    assert result.status == 'pass', result


def test_advisory_process_unknown_does_not_block_measured_error_requirements():
    from aiad_quality.spec import verification_ready
    from aiad_quality.process import process_checks
    from aiad_quality._checks import make_check
    spec={'process':'fdm_pla','process_settings':{'level':'warn'}, 'requirements':[{'id':'body','solids':1}]}
    ctx=SimpleNamespace(shape=Box(10,10,10), units='mm', process='fdm_pla', spec=spec, features=[])
    hard=[make_check(id='test-only',suite='hard',level='error',status='pass',message='test hard gate')]
    assert verification_ready(spec,spec_checks(ctx)+process_checks(ctx),hard,process='fdm_pla')


def test_effective_process_is_required_at_the_readiness_boundary():
    from aiad_quality.spec import verification_ready
    with pytest.raises(TypeError):
        verification_ready({}, [], [])


@pytest.mark.parametrize('shape_factory', [
    lambda: Rot(30, 20, 10) * plate(),
    lambda: plate() + Pos(0, -19, 10) * Box(60, 2, 20),
])
def test_through_hole_count_is_independent_of_outer_support_planes(shape_factory):
    result = check(shape_factory(), {'holes':{'count':4}})
    assert result.status == 'pass', result


def test_y_axis_grid_uses_the_xz_plane():
    result = check(Rot(90,0,0) * plate(), {'holes':{'count':4,'scope':'cylindrical','pattern':{'grid':[46,26]}}})
    assert result.status == 'pass', result


def test_shell_tunnel_count_handles_periodic_and_degenerate_surfaces():
    from build123d import Sphere, Torus
    from aiad_quality.spec_geometry import closed_shell_tunnels
    for shape, count in [(Box(10,10,10),0), (Sphere(3),0), (Torus(5,1),1), (plate(),4), (Box(10,10,10)-Sphere(2),0)]:
        assert closed_shell_tunnels(shape) == count


def test_exact_wall_threshold_on_a_proven_box_is_inclusive():
    result = process_check(Box(30, 20, 2), 'fdm_pla', 'wall_thickness', min_wall_mm=2)
    assert result.status == 'pass', result


def test_laser_circle_pass_does_not_hide_a_small_nonround_slot():
    shape = plate() - Pos(0,0,0) * Box(8,.05,8)
    result = process_check(shape, 'laser', 'minimum_feature', kerf_mm=0.1)
    assert result.status == 'skip', result


def test_injection_local_draft_does_not_certify_undercuts():
    from build123d import Rectangle, extrude
    shape = extrude(Rectangle(20,20), amount=10, taper=3)
    result = process_check(shape, 'injection', 'draft')
    assert result.status != 'pass', result


def test_unpaired_unicode_is_a_diagnostic():
    with pytest.raises(SpecValidationError):
        load_spec('requirements: [] # ' + chr(0xD800))


def test_dictionary_input_has_the_same_work_limits_as_yaml():
    nested = 1
    for _ in range(100):
        nested = [nested]
    with pytest.raises(SpecValidationError):
        load_spec({'requirements': nested})
    with pytest.raises(SpecValidationError):
        load_spec({'requirements': [dict(id=str(i), solids=1) for i in range(4000)]})
