"""Run the actual S1 worker with installed S5 hooks; no fabricated gate result."""
from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest,Check
import pytest


@pytest.mark.parametrize('spec,measured',[
    ({},False),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'body','solids':1}]},True),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'future','unimplemented':1}]},False),
    ({'requirements':[{'id':'dup','solids':1},{'id':'dup','solids':1}]},False),
])
def test_worker_uses_real_quality_checks_without_attesting_their_results(tmp_path,spec,measured):
    result=run_build(BuildRequest(source='from build123d import Box\nresult=Box(10,8,4)\n',
        project_dir=tmp_path,job_dir=tmp_path/'job',spec=spec,timeout_s=60))
    assert result.ok,result.failure
    # S1's parent clears model-process proof claims until independent
    # validation exists. Passing worker measurements are still useful, but
    # cannot promote the revision or become a trusted proof bit.
    assert result.status=='draft'
    assert result.requirements_proven is False,result.checks
    spec_checks=[check for check in result.checks if check.suite=='spec']
    assert spec_checks
    if measured:
        assert {check.id for check in spec_checks}=={req['id'] for req in spec['requirements']}
        assert all(check.status=='pass' and check.actual and check.expected for check in spec_checks)
    else:
        assert any(check.status=='skip' for check in spec_checks)
    assert all(isinstance(check,Check) for check in result.checks)
    assert not any(check.id=='spec_checks_unavailable' for check in result.checks)
    assert result.artifacts


def test_sandboxed_worker_can_place_catalog_parts_and_check_a_component_list(tmp_path):
    source = (
        "from aad_quality.components import make_envelope\n"
        "result = make_envelope('bearing.radial.608')\n"
    )
    spec = {'requirements': [
        {'id': 'bore', 'bbox': {'z': 7}},
        {'id': 'BOM', 'components': [{'id': 'bearing.radial.608', 'qty': 1}, {'custom': 'hub', 'reason': 'designed here'}]},
        {'id': 'FAKE', 'components': [{'id': 'fastener.iso4762.M3-0.5', 'length_mm': 17}]},
    ]}
    result = run_build(BuildRequest(source=source, project_dir=tmp_path, job_dir=tmp_path / 'job', spec=spec, timeout_s=60))
    # An invented part is an error-level failure, so the build itself must not pass.
    assert not result.ok and result.status == 'failed'
    checks = {check.id: check for check in result.checks if check.suite == 'spec'}
    assert checks['bore'].status == 'pass'
    assert checks['BOM'].status == 'pass'
    assert checks['FAKE'].status == 'fail' and 'nearest' in checks['FAKE'].message


def test_detailed_catalog_parts_pass_the_real_build_hard_gate_and_keep_fit_envelopes(tmp_path):
    source = r"""
from build123d import Compound, Pos
from aad_quality.components import make_detailed, make_envelope

items = []

bearing = make_detailed('bearing.radial.608', variant='2RS', lod='full')
bearing_fit = make_envelope('bearing.radial.608')
assert tuple(round(v, 6) for v in bearing.bounding_box().size) == tuple(round(v, 6) for v in bearing_fit.bounding_box().size)
items.append(bearing)
items.append(make_detailed('bearing.radial.608', variant='2RS', lod='fit'))

catalog_bearing = make_detailed('bearing.deep_groove.604', lod='full')
catalog_bearing_fit = make_envelope('bearing.deep_groove.604')
assert tuple(round(v, 6) for v in catalog_bearing.bounding_box().size) == tuple(round(v, 6) for v in catalog_bearing_fit.bounding_box().size)
items.append(catalog_bearing)

capped_bearing = make_detailed('bearing.deep_groove.604-2Z', lod='full')
capped_bearing_fit = make_envelope('bearing.deep_groove.604-2Z')
assert tuple(round(v, 6) for v in capped_bearing.bounding_box().size) == tuple(round(v, 6) for v in capped_bearing_fit.bounding_box().size)
items.append(capped_bearing)

connector = make_detailed('connector.JST-XH.4pin', lod='full')
connector_fit = make_envelope('connector.JST-XH.4pin')
assert tuple(round(v, 6) for v in connector.bounding_box().size) == tuple(round(v, 6) for v in connector_fit.bounding_box().size)
items.append(connector)
connector_lod_fit = make_detailed('connector.JST-XH.4pin', lod='fit')
assert tuple(round(v, 6) for v in connector_lod_fit.bounding_box().size) == tuple(round(v, 6) for v in connector_fit.bounding_box().size)
items.append(connector_lod_fit)

stepper = make_detailed('motor.stepper.17HS19-2004S1', lod='full')
stepper_fit = make_envelope('motor.stepper.17HS19-2004S1')
assert tuple(round(v, 6) for v in stepper.bounding_box().size) == tuple(round(v, 6) for v in stepper_fit.bounding_box().size)
items.append(stepper)
items.append(make_detailed('motor.stepper.17HS19-2004S1', lod='fit'))

gearmotor = make_detailed('motor.gearmotor.n20.100', lod='fit')
gearmotor_fit = make_envelope('motor.gearmotor.n20.100')
gearmotor_body = max(gearmotor.solids(), key=lambda solid: solid.volume)
assert tuple(round(v, 6) for v in gearmotor_body.bounding_box().size) == tuple(round(v, 6) for v in gearmotor_fit.bounding_box().size)
gearmotor_shaft = min(gearmotor.solids(), key=lambda solid: solid.volume)
shaft_bounds = gearmotor_shaft.bounding_box()
assert tuple(round(v, 6) for v in shaft_bounds.size) == (3.0, 3.0, 10.0)
assert round(shaft_bounds.min.Z, 6) == 25.0
items.append(gearmotor)

cap_screw = make_detailed('fastener.iso4762.M3-0.5', length_mm=8)
cap_fit = make_envelope('fastener.iso4762.M3-0.5', length_mm=8)
assert tuple(round(v, 6) for v in cap_screw.bounding_box().size) == tuple(round(v, 6) for v in cap_fit.bounding_box().size)
items.append(cap_screw)

countersunk = make_detailed('fastener.iso10642.M3-0.5', length_mm=8)
countersunk_fit = make_envelope('fastener.iso10642.M3-0.5', length_mm=8)
assert tuple(round(v, 6) for v in countersunk.bounding_box().size) == tuple(round(v, 6) for v in countersunk_fit.bounding_box().size)
items.append(countersunk)

inch_socket = make_detailed('fastener.asme_b18.3.10-32', length_in=0.5)
inch_socket_fit = make_envelope('fastener.asme_b18.3.10-32', length_in=0.5)
assert tuple(round(v, 6) for v in inch_socket.bounding_box().size) == tuple(round(v, 6) for v in inch_socket_fit.bounding_box().size)
items.append(inch_socket)

result = Compound(children=[Pos(index * 80, 0, 0) * item for index, item in enumerate(items)])
"""
    built = run_build(BuildRequest(
        source=source,
        kind="assembly",
        project_dir=tmp_path,
        job_dir=tmp_path / "job",
        timeout_s=180,
    ))
    assert built.ok, built.failure
    hard = [check for check in built.checks if check.suite == "hard" and check.level == "error"]
    assert hard and all(check.status == "pass" for check in hard), hard
    assert built.context is not None
    details = built.context.meta["aad_component_geometries"]
    assert {item["component_id"] for item in details} == {
        "bearing.radial.608",
        "bearing.deep_groove.604",
        "bearing.deep_groove.604-2Z",
        "connector.JST-XH.4pin",
        "motor.stepper.17HS19-2004S1",
        "motor.gearmotor.n20.100",
        "fastener.iso4762.M3-0.5",
        "fastener.iso10642.M3-0.5",
        "fastener.asme_b18.3.10-32",
    }
    assert all(item["catalog_evidence"]["status"] for item in details)
    capped_record = next(item for item in details if item["component_id"] == "bearing.deep_groove.604-2Z")
    assert capped_record["selection"]["variant"] == "ZZ"
    screw = next(item for item in details if item["component_id"] == "fastener.iso4762.M3-0.5")
    assert screw["thread"] == {"designation": "M3 × 0.5", "representation": "cosmetic"}
    inch_screw = next(item for item in details if item["component_id"] == "fastener.asme_b18.3.10-32")
    assert inch_screw["selection"] == {"length_in": 0.5}
    assert inch_screw["thread"]["designation"] == "10-32"


def test_detailed_motor_without_catalog_shaft_dimensions_refuses_to_invent(tmp_path):
    source = (
        "from aad_quality.components import make_detailed\n"
        "result = make_detailed('motor.servo.SG90')\n"
    )
    built = run_build(BuildRequest(
        source=source,
        project_dir=tmp_path,
        job_dir=tmp_path / "job",
        timeout_s=60,
    ))
    assert not built.ok
    assert built.failure and "lacks catalog output-shaft diameter/length" in built.failure.kernel_message


def test_vendor_step_geometry_uses_the_recorded_datum_and_keeps_catalog_evidence(tmp_path):
    from build123d import Align, Box, Pos, export_step

    imports = tmp_path / "imports"
    imports.mkdir()
    asset_path = imports / "servo.step"
    assert export_step(
        Pos(10, 20, 30) * Box(23.1, 12.2, 31, align=(Align.CENTER, Align.CENTER, Align.MIN)),
        str(asset_path),
    )
    source = r"""
from aad_quality.components import make_detailed, make_envelope
result = make_detailed(
    'motor.servo.SG90', vendor_step='servo.step', origin_mm=(10, 20, 30),
    vendor_evidence={'source': 'fixture supplier', 'document': 'fixture drawing rev A'},
)
expected = make_envelope('motor.servo.SG90')
assert tuple(round(v, 6) for v in result.bounding_box().size) == tuple(round(v, 6) for v in expected.bounding_box().size)
"""
    built = run_build(BuildRequest(
        source=source,
        project_dir=tmp_path,
        job_dir=tmp_path / "job",
        timeout_s=120,
    ))
    assert built.ok, built.failure
    assert built.metrics["bbox"] == [23.1, 12.2, 31.0]
    details = built.context.meta["aad_component_geometries"]
    assert len(details) == 1
    vendor = details[0]
    assert vendor["geometry_source"] == "vendor_step"
    assert vendor["vendor_import"] == {
        "asset": "servo.step",
        "origin_mm": [10.0, 20.0, 30.0],
        "evidence": {"source": "fixture supplier", "document": "fixture drawing rev A"},
    }
    assert vendor["catalog_evidence"]["status"]


def test_detailed_unsupported_bearing_family_does_not_receive_ball_bearing_internals(tmp_path):
    source = (
        "from aad_quality.components import make_detailed\n"
        "result = make_detailed('bearing.angular_contact.7200_BECBP')\n"
    )
    built = run_build(BuildRequest(
        source=source,
        project_dir=tmp_path,
        job_dir=tmp_path / "job",
        timeout_s=60,
    ))
    assert not built.ok
    assert built.failure and "not a registered radial deep-groove bearing family" in built.failure.kernel_message


def test_motor_pilot_does_not_overlap_the_shaft():
    from aad.cad import overlap_volume
    from aad_quality.components import make_detailed

    solids = make_detailed("motor.stepper.17HS19-2004S1", lod="fit").solids()
    assert len(solids) == 3
    pairs = [(0, 1), (0, 2), (1, 2)]
    assert sum(overlap_volume(solids[a], solids[b]) for a, b in pairs) < 1e-6


@pytest.mark.parametrize("component_id, ways", [
    ("connector.idc.2x5", 10),
    ("connector.Molex-MicroFit.4pin", 4),
    ("connector.pinheader.2.54.2x5", 10),
])
def test_multi_row_connectors_build_with_catalog_rows(component_id, ways):
    from aad_quality.components import make_detailed

    assert len(make_detailed(component_id, lod="fit").solids()) == ways + 1


def test_vendor_step_refuses_other_formats():
    from aad_quality.components import make_detailed

    with pytest.raises(ValueError, match=r"\.step or \.stp"):
        make_detailed("motor.stepper.17HS19-2004S1", vendor_step="supplier.brep", vendor_evidence={"source": "maker"})


def test_worker_records_and_checks_geometrically_validated_assembly_joints(tmp_path):
    source = (
        "from aad import *\n"
        "asm = assembly('stack')\n"
        "asm.add('frame', Box(10, 10, 2))\n"
        "asm.add('cover', Box(8, 8, 2), at=Pos(0, 0, 2))\n"
        "asm.connect('frame', 'cover', 'bolted', tolerance=0.01, fasteners=['M3x8'])\n"
        "result = asm\n"
    )
    spec = {'units': 'mm', 'requirements': [
        {'id': 'assembly_paths', 'connected': {'ground': 'frame', 'tolerance': 0.1}},
    ]}
    result = run_build(BuildRequest(
        source=source,
        project_dir=tmp_path,
        job_dir=tmp_path / 'assembly-job',
        kind='assembly',
        spec=spec,
        timeout_s=60,
    ))

    assert result.ok, result.failure
    check = next(item for item in result.checks if item.id == 'assembly_paths')
    assert check.status == 'pass', check
    assert 'Geometric contact proves touching only' in check.actual
    assembly = result.context.meta['assembly']
    assert assembly['joints'] == [{
        'a': 'frame',
        'b': 'cover',
        'kind': 'bolted',
        'details': {'tolerance': 0.01, 'fasteners': ['M3x8']},
    }]
