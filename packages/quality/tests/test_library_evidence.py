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
                       thread_zone_xy_mm=None, solids=1, material_probes=[])
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
