from build123d import Box, Cylinder, Pos, Rot, Align
import pytest
from aiad_drafting.features import measure_cylinders


def test_stepped_shaft_has_exact_diameters_and_axial_stations():
    shaft = Cylinder(20,12, align=(Align.CENTER,Align.CENTER,Align.MIN))
    shaft += Pos(0,0,12)*Cylinder(12,28, align=(Align.CENTER,Align.CENTER,Align.MIN))
    shaft += Pos(0,0,40)*Cylinder(8,10, align=(Align.CENTER,Align.CENTER,Align.MIN))
    features = measure_cylinders(shaft)
    stages = sorted((f.station_min, f.station_max, f.dimension_value) for f in features)
    assert stages == [(0,12,40), (12,40,24), (40,50,16)]
    assert all(f.full_circle and not f.interior for f in features)
    assert sorted(f.length for f in features) == [10,12,28]
    assert all(f.dimension()["source"]["face_indices"] for f in features)


def test_blind_bore_length_is_cylindrical_span_without_unproven_through_label():
    block = Box(40,30,12) - Pos(0,0,5)*Cylinder(4,8)
    features = measure_cylinders(block)
    assert len(features) == 1
    bore = features[0]
    assert bore.interior and bore.full_circle
    assert bore.dimension_value == 8
    assert bore.length == pytest.approx(5)
    assert "THRU" not in bore.dimension()["display"]


def test_half_round_reports_radius_instead_of_full_diameter():
    half = Cylinder(10,6) & (Pos(5,0,0)*Box(10,25,8))
    features = measure_cylinders(half)
    assert len(features) == 1
    assert not features[0].full_circle
    assert features[0].dimension_value == 10
    assert features[0].dimension()["kind"] == "cylinder_radius"


def test_rotated_axis_and_inch_values_remain_geometric():
    shape = Pos(8,3,2)*Rot(0,90,0)*Cylinder(2,7)
    feature = measure_cylinders(shape,"in")[0]
    assert feature.axis == pytest.approx((1,0,0))
    assert feature.axis_origin == pytest.approx((0,3,2))
    assert feature.station_min == pytest.approx(4.5)
    assert feature.station_max == pytest.approx(11.5)
    assert feature.dimension_value == 4 and feature.units == "in"
