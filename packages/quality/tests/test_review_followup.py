"""Regression witnesses from the second independent review of PR #34."""
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos, Rot, Sphere, Torus

from aad_quality.spec import spec_checks


def check(shape, body):
    return spec_checks(SimpleNamespace(shape=shape, units='mm', spec={'requirements':[dict(id='followup', **body)]}))[0]


@pytest.mark.parametrize('shape', [
    Box(30,30,20)-Cylinder(2,8)-Pos(0,0,6)*Sphere(3)-Pos(0,0,-6)*Sphere(3),
    Box(20,20,6)-Cylinder(2,6-2*5e-6),
])
def test_sealed_cavities_never_establish_an_exterior_bore(shape):
    result = check(shape, {'holes':{'count':1,'scope':'cylindrical'}})
    assert result.status == 'fail', result


@pytest.mark.parametrize('shape', [
    Box(20,20,10)-Cylinder(1,12)-Pos(0,0,4)*Cylinder(5,4),
    Box(20,20,10)-Cylinder(1,12)-Pos(0,0,4)*Cylinder(5,4)-Pos(0,0,-4)*Cylinder(5,4),
])
def test_requested_through_diameter_must_clear_the_whole_channel(shape):
    assert check(shape, {'holes':{'count':1,'diameter':10,'through':True}}).status == 'fail'
    assert check(shape, {'holes':{'count':1,'diameter':10,'through':False}}).status == 'pass'
    assert check(shape, {'holes':{'count':1,'diameter':2,'through':True}}).status == 'pass'


def test_cross_drilling_genus_is_not_reported_as_a_bore_count_failure():
    shape = Box(20,20,20)-Cylinder(2,30)-Rot(90,0,0)*Cylinder(2,30)
    result = check(shape, {'holes':{'count':2}})
    assert result.status == 'skip', result


def test_an_internal_toroidal_void_is_not_an_exterior_tunnel():
    result = check(Box(30,30,20)-Torus(5,1), {'holes':{'count':0}})
    assert result.status == 'pass', result


@pytest.mark.parametrize('bounds', ['40 ± 0', {'nominal':40,'tolerance':0}, {'min':40,'max':40}])
def test_explicit_zero_tolerances_remain_strict(bounds):
    assert check(Box(40.0000005,10,10), {'bbox':{'x':bounds}}).status == 'fail'


@pytest.mark.parametrize('bounds', ['4 ± 0', {'nominal':4,'tolerance':0}, {'min':4,'max':4}])
def test_explicit_zero_diameter_tolerances_remain_strict(bounds):
    assert check(Box(20,20,6)-Cylinder(2.00000025,8), {'holes':{'count':1,'diameter':bounds}}).status == 'fail'
