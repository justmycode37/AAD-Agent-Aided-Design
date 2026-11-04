"""Parent audit of spec/process integration and plausible false proofs."""
from types import SimpleNamespace
import pytest
from build123d import Axis, Box, Cylinder, GridLocations, Hole, BuildPart, Pos, Rot, Rectangle, Torus, export_step
from aiad_quality._checks import make_check,record
from aiad_quality.spec import load_spec,spec_checks,verification_ready
from aiad_quality.process import process_checks
from aiad_quality import spec_geometry


def context(shape,spec,**kwargs):
    values=dict(shape=shape,spec=spec,units='mm',features=[],params={},metrics={},process=None,material=None)
    values.update(kwargs)
    return SimpleNamespace(**values)


def hard():
    return [make_check(id='unit-hard',suite='hard',level='error',status='pass',message='Simulated hard gate for a coverage unit test')]


def test_process_settings_can_be_evaluated_alongside_spec():
    spec={'process':'fdm_pla','process_settings':{'min_wall_mm':.5},'requirements':[{'id':'size','bbox':{'x':10}}]}
    ctx=context(Box(10,10,10),spec)
    checks=spec_checks(ctx)+process_checks(ctx)
    assert all(record(c)['status']=='pass' for c in checks),checks
    assert verification_ready(spec,checks,hard())


@pytest.mark.parametrize('value',[{'max':'1e308 in'},'1e308 ± 1e308',10**500])
def test_converted_or_expanded_overflow_is_invalid(value):
    spec={'requirements':[{'id':'size','bbox':{'x':value}}]}
    with pytest.raises(ValueError):load_spec(spec)
    assert not verification_ready(spec,spec_checks(context(Box(1,1,1),spec)),hard())


def test_caller_shape_attributes_cannot_spoof_native_bbox_or_solid_count():
    part=Box(5,5,5)
    part.bounding_box=lambda: Box(10,10,10).bounding_box()
    part.solids=lambda: [part,part]
    spec={'requirements':[{'id':'size','bbox':{'x':10}},{'id':'body','solids':2}]}
    checks=spec_checks(context(part,spec))
    assert [record(c)['status'] for c in checks]==['fail','fail']


def test_zero_volume_cannot_prove_surface_symmetry():
    spec={'requirements':[{'id':'mirror','symmetric':{'plane':'YZ','tolerance':.01}}]}
    check=record(spec_checks(context(Pos(5,0)*Rectangle(2,3),spec))[0])
    assert check['status']=='skip'


def test_subresolution_shift_cannot_satisfy_zero_symmetry_tolerance():
    spec={'requirements':[{'id':'mirror','symmetric':{'plane':'YZ','tolerance':0}}]}
    check=record(spec_checks(context(Pos(1e-7,0,0)*Box(10,8,6),spec))[0])
    assert check['status']=='skip',check
    assert not verification_ready(spec,[check],hard())


@pytest.mark.parametrize('round_bores',[0,4])
def test_opening_count_cannot_ignore_a_square_through_opening(round_bores):
    shape=Box(60,40,6)-Box(6,6,8)
    if round_bores:
        for x in (-23,23):
            for y in (-13,13):shape=shape-Pos(x,y,0)*Cylinder(2.2,8)
    spec={'requirements':[{'id':'openings','holes':{'count':round_bores}}]}
    check=record(spec_checks(context(shape,spec))[0])
    assert check['status']=='skip',check
    assert 'noncircular' in check['message']
    # A caller may explicitly ask only about cylindrical bores. The check
    # must expose that narrower scope rather than imply all openings passed.
    spec['requirements'][0]['holes']['scope']='cylindrical'
    check=record(spec_checks(context(shape,spec))[0])
    assert check['status']=='pass',check
    assert 'Cylindrical bore' in check['message']
    assert 'other opening types are outside' in check['expected']


def test_polygonal_tunnel_across_face_junctions_does_not_prove_zero_openings():
    shape=Box(20,20,20)-Rot(0,45,0)*Box(4,4,40)
    spec={'requirements':[{'id':'none','holes':{'count':0}}]}
    check=record(spec_checks(context(shape,spec))[0])
    assert check['status']=='skip',check


@pytest.mark.parametrize('scope',['polygonal',True,[],{}])
def test_invalid_hole_scope_is_rejected(scope):
    with pytest.raises(ValueError):
        load_spec({'requirements':[{'id':'none','holes':{'count':0,'scope':scope}}]})


@pytest.mark.parametrize('center,expected',[(0,'fail'),(6,'pass'),(8,'pass')])
def test_clearance_zero_still_rejects_penetration(tmp_path,center,expected):
    export_step(Pos(center,0,0)*Box(2,2,2),str(tmp_path/'reference.step'))
    spec={'requirements':[{'id':'gap','clearance':{'with':'reference.step','min':0}}]}
    check=record(spec_checks(context(Box(10,10,10),spec,project_dir=tmp_path))[0])
    assert check['status']==expected,check
    if center==0:assert 'intersection volume' in check['actual']


def test_three_by_three_grid_uses_uniform_pitch():
    with BuildPart() as bp:
        Box(40,40,6)
        with GridLocations(10,8,3,3):Hole(1)
    spec={'requirements':[{'id':'grid','holes':{'count':9,'diameter':'2 ± 0.01','pattern':{'grid':[10,8]}}}]}
    check=record(spec_checks(context(bp.part,spec))[0])
    assert check['status']=='pass',check


def test_duplicate_xy_axes_do_not_fill_a_missing_grid_position():
    shape=Box(60,40,6)
    for x,y in [(-23,-13),(23,-13)]:shape=shape-Pos(x,y,0)*Cylinder(2.2,8)
    for z in (-2.5,2.5):shape=shape-Pos(-23,13,z)*Cylinder(2.2,1)
    spec={'requirements':[{'id':'grid','holes':{'count':4,'diameter':'4.4 ± 0.01','pattern':{'grid':[46,26]}}}]}
    check=record(spec_checks(context(shape,spec))[0])
    assert check['status']=='fail',check
    assert 'duplicate XY' in check['actual']


@pytest.mark.parametrize('value',['NaN','inf mm','-Infinity','∞'])
def test_nonfinite_text_is_not_passing_evidence(value):
    spec={'requirements':[{'id':'size','bbox':{'x':10}}]}
    check=record(spec_checks(context(Box(10,10,10),spec))[0])
    check['actual']=value
    assert not verification_ready(spec,[check],hard())
    check['actual']='10 mm'
    process=dict(check,id='process.fake',suite='process',actual=value)
    assert not verification_ready(spec,[check,process],hard())


def test_custom_area_is_measured_from_brep_not_overridable_property(monkeypatch):
    shape=Box(10,8,4)
    face=shape.faces().sort_by(Axis.Z)[-1]
    proxy=SimpleNamespace(wrapped=face.wrapped,area=12345)
    monkeypatch.setattr(spec_geometry,'_resolve_selector',lambda *args:proxy)
    spec={'requirements':[{'id':'area','measure':{'kind':'area','a':'selected-face'},'equals':'80 ± 0.001'}]}
    check=record(spec_checks(context(shape,spec))[0])
    assert check['status']=='pass',check


def test_exception_evidence_and_unknown_process_settings_cannot_promote():
    spec={'process':'cnc_3axis','process_settings':{'surprise':1},'requirements':[{'id':'size','bbox':{'x':10}}]}
    ctx=context(Box(10,10,10),spec)
    assert not verification_ready(spec,spec_checks(ctx)+process_checks(ctx),hard())


def test_checks_use_engine_contract_type():
    from aiad.engine.types import Check
    check=spec_checks(context(Box(1,1,1),{'requirements':[{'id':'body','solids':1}]}))[0]
    assert isinstance(check,Check)


def test_declared_process_requires_its_own_check_evidence():
    spec={'process':'fdm_pla','requirements':[{'id':'size','bbox':{'x':10}}]}
    checks=spec_checks(context(Box(10,10,10),spec))
    assert not verification_ready(spec,checks,hard())


def test_unknown_curved_geometry_cannot_prove_absence_of_holes():
    spec={'requirements':[{'id':'none','holes':{'count':0}}]}
    assert record(spec_checks(context(Torus(5,1),spec))[0])['status']=='skip'


def test_exact_bounds_do_not_gain_an_implicit_relative_tolerance():
    spec={'requirements':[{'id':'size','bbox':{'x':1e12}}]}
    assert record(spec_checks(context(Box(1e12+.5,8,4),spec))[0])['status']=='fail'
