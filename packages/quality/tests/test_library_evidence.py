"""Adversarial evidence mutations, separate from the construction implementation."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

EVALS = Path(__file__).resolve().parents[3] / 'evals/library'
spec = importlib.util.spec_from_file_location('library_evidence', EVALS / 'evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def parts_fixture(base):
    # Schema-only fixtures. The injected measurement is explicit; these bytes
    # are never claimed to be a valid BREP by the production native audit.
    run_id = str(uuid.uuid4())
    folder = base / '.runs' / run_id
    folder.mkdir(parents=True)
    measurement = dict(valid=True, passed=True, bbox_mm=[1.,2.,3.],
                       thread_zone_xy_mm=None, solids=1, material_probes=[], geometry_witness=None)
    rows = []
    for repeat in (1,2,3):
        path = folder / f'fixture-{repeat}.brep'
        path.write_bytes(b'schema fixture only')
        rows.append(dict(id='fixture',repeat=repeat,**deepcopy(measurement),
                         artifact=path.relative_to(base).as_posix(),
                         artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(complete=True,passed=True,run_id=run_id,repetitions=3,source_sha256={},
                records=rows,summaries=evidence.summaries(rows,['fixture'])), measurement


def validate(data, base, measurement):
    evidence.validate_parts(data,base=base,expected={'fixture':(1,2,3)},source_sha256={},
                            measure=lambda *_:deepcopy(measurement))


@pytest.mark.parametrize('field',['complete','passed'])
def test_string_false_is_not_a_passing_evidence_flag(tmp_path,field):
    data,measurement=parts_fixture(tmp_path)
    data[field]='false'
    with pytest.raises(ValueError):validate(data,tmp_path,measurement)


@pytest.mark.parametrize('mutant',['short','duplicate','summary','measurement','valid_string'])
def test_missing_or_forged_trial_evidence_is_rejected(tmp_path,mutant):
    data,measurement=parts_fixture(tmp_path)
    if mutant=='short':data['records']=data['records'][:1]
    elif mutant=='duplicate':data['records'][1]=deepcopy(data['records'][0])
    elif mutant=='summary':data['summaries'][0]['n']=99
    elif mutant=='measurement':data['records'][0]['bbox_mm'][0]=9.
    else:data['records'][0]['valid']='false'
    with pytest.raises(ValueError):validate(data,tmp_path,measurement)


@pytest.mark.parametrize('mutant',['run_traversal','absolute','artifact_traversal','symlink'])
def test_evidence_paths_cannot_escape_the_fixed_run_root(tmp_path,mutant):
    data,measurement=parts_fixture(tmp_path)
    outside=tmp_path/'outside.brep';outside.write_bytes(b'not retained evidence')
    row=data['records'][0]
    if mutant=='run_traversal':data['run_id']='../../'
    elif mutant=='absolute':row['artifact']=str(outside.resolve())
    elif mutant=='artifact_traversal':row['artifact']=f'.runs/{data["run_id"]}/../../outside.brep'
    else:
        path=tmp_path/row['artifact'];path.unlink();path.symlink_to(outside)
    with pytest.raises(ValueError):validate(data,tmp_path,measurement)


def mcp_records():
    rows=[]
    for kind in evidence.KINDS:
        entry={'id':f'{kind}.first','kind':kind}
        rows.append({'args':{'action':'search','kind':kind},'response':{'ok':True,'library':{'total':1,'items':[entry],'truncated':False}}})
        rows.append({'args':{'action':'get','kind':kind,'id':entry['id']},'response':{'ok':True,'library':{'found':True,'item':deepcopy(entry)}}})
    rows.append({'args':{'action':'get','kind':'material','id':'missing'},'response':{'ok':True,'library':{'found':False,'item':None}}})
    rows.append({'args':{'action':'get','id':'../secret'},'response':{'ok':False,'failure':{'code':'BAD_INPUT','phase':'input'}}})
    return rows


@pytest.mark.parametrize('mutant',['wrong_id','wrong_kind','string_ok','missing_call','missing_control'])
def test_mcp_claims_require_the_exact_observed_sequence(mutant):
    rows=mcp_records()
    evidence.validate_mcp_records(rows)
    if mutant=='wrong_id':rows[1]['response']['library']['item']['id']='other'
    elif mutant=='wrong_kind':rows[1]['response']['library']['item']['kind']='other'
    elif mutant=='string_ok':rows[1]['response']['ok']='false'
    elif mutant=='missing_call':rows.pop()
    else:rows[-1]['response']['ok']=True
    with pytest.raises(ValueError):evidence.validate_mcp_records(rows)


def test_optimized_python_cannot_strip_evidence_validation():
    program='''import importlib.util
spec=importlib.util.spec_from_file_location('evidence', __import__('sys').argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
try:
    module.validate_parts({'complete':'false','passed':'false','records':[]},base='.',expected={},source_sha256={},measure=lambda *_:None)
except ValueError:
    pass
else:
    raise SystemExit(99)
'''
    result=subprocess.run([sys.executable,'-O','-c',program,str(EVALS/'evidence.py')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_native_reaudit_does_not_accept_an_unparsed_artifact(tmp_path):
    pytest.importorskip('build123d')
    artifact=tmp_path/'not-a-brep.brep';artifact.write_text('not a BREP')
    program="import sys; sys.path.insert(0,sys.argv[1]); import probe_parts; probe_parts.measure_artifact(sys.argv[2],'bearing.deep_groove_ball.skt')"
    result=subprocess.run([sys.executable,'-c',program,str(EVALS),str(artifact)],capture_output=True,text=True,timeout=30)
    assert result.returncode!=0, 'Unparsed bytes were accepted as BREP evidence'


@pytest.mark.parametrize('text',['{"passed":false,"passed":true}','{"x":NaN}','{"x":1e900}'])
def test_ambiguous_or_nonfinite_evidence_json_is_rejected(tmp_path,text):
    file=tmp_path/'bad.json';file.write_text(text)
    with pytest.raises(ValueError):evidence.load_document(file)


@pytest.mark.parametrize('case',['smooth_thread','annulus_bearing','wrong_pitch','wrong_hand','wrong_profile'])
def test_native_part_witnesses_reject_same_envelope_impostors(tmp_path,monkeypatch,case):
    pytest.importorskip('bd_warehouse')
    from build123d import Align,Cylinder,export_brep
    monkeypatch.syspath_prepend(str(EVALS))
    import probe_parts
    if case=='annulus_bearing':
        shape=Cylinder(11,7,align=(Align.CENTER,Align.CENTER,Align.MIN))-Cylinder(4,7,align=(Align.CENTER,Align.CENTER,Align.MIN))
        part_id='bearing.deep_groove_ball.skt'
    elif case=='smooth_thread':
        shape=Cylinder(3,8,align=(Align.CENTER,Align.CENTER,Align.MIN))
        part_id='thread.iso_metric_external'
    elif case in ('wrong_pitch','wrong_hand'):
        from bd_warehouse.thread import IsoThread
        shape=IsoThread(major_diameter=6,pitch=.8 if case=='wrong_pitch' else 1,length=8,
                        hand='left' if case=='wrong_hand' else 'right',end_finishes=('square','square'))
        part_id='thread.iso_metric_external'
    else:
        from bd_warehouse.thread import Thread
        shape=Thread(apex_radius=3,apex_width=.3,root_radius=3-5/8*(3**.5/2),root_width=.75,
                     pitch=1,length=8,end_finishes=('square','square'))
        part_id='thread.iso_metric_external'
    artifact=tmp_path/f'{case}.brep';export_brep(shape,str(artifact))
    observed=probe_parts.measure_artifact(artifact,part_id)
    assert observed['valid'] and not observed['passed'],observed
    witness=observed['geometry_witness']
    if case=='annulus_bearing':assert witness['balls']==[]
    elif case=='wrong_profile':
        assert witness['helical_edges']['passed'] and not witness['profile']['passed']
    else:assert not witness['helical_edges']['passed']


@pytest.mark.parametrize('part_id',['bearing.deep_groove_ball.skt','thread.iso_metric_external'])
def test_real_default_parts_pass_the_added_native_geometry_witnesses(tmp_path,monkeypatch,part_id):
    pytest.importorskip('bd_warehouse')
    from build123d import export_brep
    from aad_quality.standard_parts import make_part
    monkeypatch.syspath_prepend(str(EVALS))
    import probe_parts
    artifact=tmp_path/'actual.brep';export_brep(make_part(part_id),str(artifact))
    observed=probe_parts.measure_artifact(artifact,part_id)
    assert observed['passed'] and observed['geometry_witness']['passed'],observed
