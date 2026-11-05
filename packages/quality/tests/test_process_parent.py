"""Independent process regressions, including the teardown's access control."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos, RectangleRounded, extrude, Compound, Rot
from aad_quality._checks import record
from aad_quality.process import process_checks
from aad_quality import process_geometry

ROOT=Path(__file__).resolve().parents[3]


def context(shape,process,settings=None,units='mm',spec_process=None):
    spec={'process_settings':settings or {}}
    if spec_process is not None:spec['process']=spec_process
    return SimpleNamespace(shape=shape,process=process,units=units,spec=spec,features=[],params={},metrics={})


def fixture(name,unblocked=False):
    code=(ROOT/'evals/teardown/tasks'/f'{name}.py').read_text()
    if unblocked:code=code.replace('Box(22, 16, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))','pass')
    namespace={}
    exec(compile(code,'trusted_test_fixture','exec'),namespace)
    return namespace['result']


def by_id(ctx):
    return {record(check)['id']:record(check) for check in process_checks(ctx)}


def test_coaxial_access_distinguishes_open_counterbore_from_bridge():
    settings={'level':'error','tool_diameter_mm':3,'tool_axis':[0,0,1]}
    for shape,expected in ((fixture('plate'),'pass'),(fixture('blocked_counterbore',unblocked=True),'pass'),(fixture('blocked_counterbore'),'fail')):
        check=by_id(context(shape,'cnc_3axis',settings))['process.cnc_3axis.hole_approach_probe']
        assert check['status']==expected,check
        assert 'mm³' in check['actual']
        assert check['heuristic'] is True
        assert 'coaxial' in check['assumptions']


def test_external_boss_is_not_a_hole():
    shape=Box(30,30,6)+Pos(0,0,4)*Cylinder(3,4)
    check=by_id(context(shape,'cnc_3axis'))['process.cnc_3axis.hole_approach_probe']
    assert check['status']=='skip'
    assert 'no inward' in check['actual']


def test_declared_tool_that_does_not_fit_fails():
    check=by_id(context(fixture('plate'),'cnc_3axis',{'level':'error','tool_diameter_mm':6}))['process.cnc_3axis.hole_approach_probe']
    assert check['status']=='fail'


def test_local_wall_disproof_and_overhang_disproof():
    checks=by_id(context(Box(10,10,.5),'fdm_pla',{'level':'error','min_wall_mm':1.2}))
    assert checks['process.fdm_pla.wall_thickness']['status']=='fail'
    bridge=by_id(context(fixture('blocked_counterbore'),'fdm_pla',{'level':'error','min_wall_mm':1}))
    assert bridge['process.fdm_pla.overhang']['status']=='fail'


def test_inch_wall_rays_convert_to_mm():
    check=by_id(context(Box(1,1,.02),'sla',{'level':'error','min_wall_mm':1},units='in'))['process.sla.wall_thickness']
    assert check['status']=='fail'
    assert '0.508' in check['actual']


def test_wall_thinner_than_ray_offset_cannot_pass():
    check=by_id(context(Box(10,10,.005),'sla',{'level':'error','min_wall_mm':.1}))['process.sla.wall_thickness']
    assert check['status']!='pass'


def test_inside_corner_radius_is_not_an_external_fillet():
    shape=Box(30,30,8,align=(Align.CENTER,Align.CENTER,Align.MIN))-Pos(0,0,4)*extrude(RectangleRounded(20,20,2),amount=5)
    check=by_id(context(shape,'cnc_3axis',{'level':'error','minimum_internal_radius_mm':3}))['process.cnc_3axis.internal_radius']
    assert check['status']=='fail',check
    assert '2.000' in check['actual']


def test_laser_circular_cut_threshold():
    shape=fixture('plate')
    for kerf,expected in ((1,'pass'),(3,'fail')):
        check=by_id(context(shape,'laser',{'level':'error','kerf_mm':kerf}))['process.laser.minimum_feature']
        assert check['status']==expected,check


def test_sheet_metal_bend_pair_has_measured_stock_and_radius():
    alignment=(Align.MIN,Align.MIN,Align.MIN)
    shape=Cylinder(7,10,arc_size=90,align=alignment)-Cylinder(5,10,arc_size=90,align=alignment)
    checks=by_id(context(shape,'sheet_metal',{'level':'error','stock_thickness_mm':2,'minimum_inside_bend_radius_mm':6}))
    assert checks['process.sheet_metal.stock_thickness']['status']=='pass',checks
    assert checks['process.sheet_metal.inside_bend_radius']['status']=='fail',checks


def test_zero_planar_draft_fails_configured_injection_screen():
    check=by_id(context(Box(10,10,10),'injection',{'level':'error'}))['process.injection.draft']
    assert check['status']=='fail'


@pytest.mark.parametrize('process',['fdm_pla','sla','cnc_3axis','sheet_metal','laser','injection'])
def test_every_process_check_explains_its_evidence(process):
    checks=process_checks(context(Box(10,10,3),process))
    assert checks
    for check in checks:
        check=record(check)
        assert check['heuristic'] is True
        assert check['assumptions'] and check['expected'] and check['actual'] and check['fix_hint']


@pytest.mark.parametrize('settings',[{'tool_diameter_mm':float('nan')},{'tool_diameter_mm':True},{'tool_axis':[0,0,0]},{'surprise':1}])
def test_invalid_settings_withhold_evidence(settings):
    checks=process_checks(context(Box(10,10,3),'cnc_3axis',settings))
    assert all(record(c)['level']=='error' and record(c)['status']!='pass' for c in checks)


def test_unknown_process_and_conflicting_context_are_errors():
    for ctx in (context(Box(1,1,1),'unknown'),context(Box(1,1,1),'cnc_3axis',spec_process='fdm_pla')):
        assert all(record(c)['level']=='error' and record(c)['status']!='pass' for c in process_checks(ctx))
    assert process_checks(context(Box(1,1,1),None))==[]
    assert process_checks(context(Box(1,1,1),None,spec_process='injection'))


def test_failed_evaluator_cannot_hide_as_optional_warning(monkeypatch):
    import aad_quality.process as process
    monkeypatch.setattr(process,'_additive_checks',lambda *args: (_ for _ in ()).throw(RuntimeError('broken kernel query')))
    check=record(process_checks(context(Box(1,1,1),'fdm_pla'))[0])
    assert check['level']=='error' and check['status']=='skip'


def test_no_surface_samples_is_unknown(monkeypatch):
    monkeypatch.setattr(process_geometry,'_surface_samples_for_face',lambda *args: [])
    check=by_id(context(Box(1,1,1),'fdm_pla',{'level':'error'}))['process.fdm_pla.overhang']
    assert check['status']=='skip'


def test_collision_threshold_uses_actual_small_tool_radius():
    # The bridge intrudes by 0.015 mm into a 0.1 mm diameter probe. Its
    # intersection volume exceeds a 0.01 mm length tolerance for that probe.
    # Both approaches are obstructed; one clear side suffices for a through bore.
    shape=fixture('plate')+Pos(-23,-13,3.0075)*Box(2,2,.015)+Pos(-23,-13,-3.0075)*Box(2,2,.015)
    check=by_id(context(shape,'cnc_3axis',{'level':'error','tool_diameter_mm':.1}))['process.cnc_3axis.hole_approach_probe']
    assert check['status']=='fail',check


def test_disconnected_quarter_cylinders_are_not_a_bend():
    alignment=(Align.MIN,Align.MIN,Align.MIN)
    first=Cylinder(7,10,arc_size=90,align=alignment)
    second=Rot(0,0,180)*Cylinder(5,10,arc_size=90,align=alignment)
    shape=Compound(children=[first,second])
    checks=by_id(context(shape,'sheet_metal',{'level':'error','stock_thickness_mm':2,'minimum_inside_bend_radius_mm':1}))
    assert all(check['status']!='pass' for check in checks.values()),checks


def test_oblique_build_contact_is_not_an_overhang():
    shape=Rot(0,45,0)*Box(10,10,3)
    check=by_id(context(shape,'fdm_pla',{'level':'error','build_direction':[1,0,1]}))['process.fdm_pla.overhang']
    assert check['status']=='pass',check


def test_partial_sample_query_error_cannot_become_pass(monkeypatch):
    original=process_geometry._unit
    calls=0
    def fail_one(vector):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('one classified sample query failed')
        return original(vector)
    monkeypatch.setattr(process_geometry,'_unit',fail_one)
    check=by_id(context(Box(10,10,3),'fdm_pla',{'level':'error'}))['process.fdm_pla.overhang']
    assert check['status']=='skip',check


def test_cylinder_enumeration_cap_is_visible():
    check=by_id(context(fixture('plate'),'cnc_3axis',{'level':'error','max_faces':2}))['process.cnc_3axis.hole_approach_probe']
    assert check['status']=='skip' and 'cap' in check['actual']


def test_overridden_shape_methods_are_not_process_measurements():
    shape=Box(10,10,.5)
    shape.faces=lambda:Box(100,100,100).faces()
    shape.bounding_box=lambda:Box(100,100,100).bounding_box()
    check=by_id(context(shape,'sla',{'level':'error','min_wall_mm':1.2}))['process.sla.wall_thickness']
    assert check['status']=='fail',check


def test_declared_assumptions_do_not_get_arbitrary_manufacturing_maxima():
    checks=by_id(context(Box(10,10,10),'fdm_pla',{'level':'error','nozzle_diameter_mm':.4,'wall_line_count':20}))
    assert checks['process.fdm_pla.wall_thickness']['status']=='pass'
    check=by_id(context(Box(10,10,10),'injection',{'level':'error','min_draft_deg':46}))['process.injection.draft']
    assert check['status']=='fail' # measured zero draft, rather than an invented 45° input limit
